import asyncio
import contextlib
import io
import os
import tempfile
from collections import Counter
from pathlib import Path

from typing import Annotated
from fastmcp import FastMCP, Context
from fastmcp.server.tasks import TaskConfig
from cml2graph.core.formats.gspan_fmt import GSpanFormat
from cml2graph.core.formats.node_link_fmt import NodeLinkFormat

from app.utils.logger import setup_logging
from app.core.state import (
    SESSIONS,
    plugin_manager as _plugin_manager,
    get_session,
)
from app.core.visualization import generate_html_visualization
from app.utils.cminer_patterns_helper import (
    PatternInfo,
    parse_cminer_output,
    pattern_to_tve_gdata,
)

logger = setup_logging("DEBUG", __name__)

mcp = FastMCP("cminer")


# ================== Parameter tools ======================

@mcp.tool()
async def cminer_get_status(ctx: Context = None) -> str:
    """
    Use this to get current CMiner status, configuration, parameters.
    Use it before changing parameters or run cminer to search for patterns
    to know the actual status of cminer and its parameters.
    """
    cminer = ctx.lifespan_context["cminer"]
    return cminer.get_info()


@mcp.tool()
async def cminer_set_parameter_db_file(
    db_file: Annotated[str, "Path to the database file."],
    ctx: Context = None,
) -> str:
    """Use this to set the database file path for CMiner."""
    cminer = ctx.lifespan_context["cminer"]
    cminer.set_db_file(db_file)
    return f"Database file set to: {db_file}"


@mcp.tool()
async def cminer_set_parameter_min_support(
    support: Annotated[float, "Minimum support threshold."],
    ctx: Context = None,
) -> str:
    """Use this to set the minimum support threshold for the pattern mining through CMiner."""
    cminer = ctx.lifespan_context["cminer"]
    cminer.set_min_support(support)
    return f"Minimum support set to: {support}"


@mcp.tool()
async def cminer_set_parameter_min_nodes(
    min_nodes: Annotated[int, "Minimum number of nodes in patterns."],
    ctx: Context = None,
) -> str:
    """Use this to set the minimum number of nodes for patterns through CMiner."""
    cminer = ctx.lifespan_context["cminer"]
    try:
        cminer.set_min_nodes(min_nodes)
    except ValueError as e:
        return f"Error: {e}"
    return f"Minimum nodes set to: {min_nodes}"


@mcp.tool()
async def cminer_set_parameter_max_nodes(
    max_nodes: Annotated[int, "Maximum number of nodes in patterns."],
    ctx: Context = None,
) -> str:
    """Use this to set the maximum number of nodes for patterns through CMiner."""
    cminer = ctx.lifespan_context["cminer"]
    try:
        cminer.set_max_nodes(max_nodes)
    except ValueError as e:
        return f"Error: {e}"
    return f"Maximum nodes set to: {max_nodes}"


# ====================== Mining tools ======================

@mcp.tool(task=TaskConfig(mode="optional"))
async def cminer_mine_patterns(
    ctx: Context,
) -> str:
    """
    Mine patterns across ALL models in the session list (with their current filters applied).
    Each model's current_graph is exported to a temp file and batch-loaded into CMiner.
    If no models are in the list, falls back to the configured CMiner db_file.
    """

    logger.debug(f"Session {ctx.session_id}: starting CMiner run...")
    if not ctx or not ctx.session_id:
        return "Error: No session ID in MCP context."

    session_id = ctx.session_id
    cminer     = ctx.lifespan_context["cminer"]
    session = get_session(ctx)

    models = session.get("models", {})
    tmp_files: list[str] = []
    nlf = NodeLinkFormat()

    # Reset CMiner state for a fresh run
    cminer.db = []
    from CMiner.CMinerAPI import DFSStack
    cminer.stack = DFSStack(
        cminer.stack.min_nodes,
        cminer.stack.max_nodes,
        cminer.stack.output_options,
    )

    if models:
        # Batch mode: export each model's current_graph to a temp file,
        # then load them all into CMiner's db
        for name, entry in models.items():
            graph = entry["current_graph"]
            json_text = nlf.render(graph)
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            )
            tmp.write(json_text)
            tmp.close()
            tmp_files.append(tmp.name)
            logger.debug(
                f"Session {session_id}: model '{name}' — "
                f"{graph.number_of_nodes()} nodes, "
                f"{graph.number_of_edges()} edges → {tmp.name}"
            )
            # Load this file into CMiner's db (accumulate)
            cminer.set_db_file(tmp.name)
            cminer._read_graphs_from_file()

        await ctx.info(
            f"Batch mining {len(models)} model(s): "
            + ", ".join(models.keys())
        )

        # Now run the mining steps directly (skip mine() which would re-read the last file)
        logger.debug(
            f"Session {session_id}: batch CMiner run — "
            f"{len(cminer.db)} graph(s) in db, "
            f"min_support={cminer.min_support}, "
            f"min_nodes={cminer.stack.min_nodes}, "
            f"max_nodes={cminer.stack.max_nodes}"
        )
        await ctx.info(
            f"starting CMiner... {len(cminer.db)} graph(s), "
            f"min_support={cminer.min_support}, "
            f"min_nodes={cminer.stack.min_nodes}, "
            f"max_nodes={cminer.stack.max_nodes}"
        )

        buf = io.StringIO()
        # Save original support so _parse_support() doesn't corrupt it across runs
        original_support = cminer.min_support
        try:
            with contextlib.redirect_stdout(buf):
                cminer._parse_support()
                cminer._find_start_patterns()
                await asyncio.to_thread(cminer.mine_all_patterns)
        finally:
            cminer.min_support = original_support
            for f in tmp_files:
                os.unlink(f)

        # Collect output from the StringSolutionSaver
        raw = cminer.stack.solution_saver.get_results() if hasattr(cminer.stack.solution_saver, 'get_results') else ""
        output_text: str = raw if isinstance(raw, str) and raw.strip() else buf.getvalue()
    else:
        # No models loaded — fall back to configured db_file
        logger.debug(
            f"Session {session_id}: no models in list, using db_file='{cminer.db_file}'"
        )
        await ctx.info(
            f"starting CMiner... parameters: "
            f"db_file='{cminer.db_file}', "
            f"min_support={cminer.min_support}, "
            f"min_nodes={cminer.stack.min_nodes}, "
            f"max_nodes={cminer.stack.max_nodes}"
        )
        buf = io.StringIO()
        original_support = cminer.min_support
        try:
            with contextlib.redirect_stdout(buf):
                raw = await asyncio.to_thread(cminer.mine)
        finally:
            cminer.min_support = original_support
        output_text: str = raw if isinstance(raw, str) and raw.strip() else buf.getvalue()

    logger.debug(f"Raw CMiner output (first 1000 chars):\n{output_text[:1000]}")

    patterns = parse_cminer_output(output_text)

    session["patterns"] = [p.__dict__ for p in patterns]
    session["html"] = None

    await ctx.info(f"Session {session_id}: {len(patterns)} patterns stored.")
    return (
        f"CMiner completed. Found {len(patterns)} pattern(s).\n"
        f"Pattern data resource URI: cminer://sessions/{session_id}/patterns"
    )


@mcp.tool()
async def cminer_generate_visualization(ctx: Context) -> str:
    """
    Generate an interactive HTML visualization.
    - If patterns have been mined, shows the pattern browser.
    - If models are loaded, shows a Models tab with a clickable list of all models.
    - Both tabs are shown together when both are available.
    """
    logger.debug(f"Session {ctx.session_id}: generating visualization...")
    if not ctx or not ctx.session_id:
        return "Error: No session ID in MCP context."

    session_id = ctx.session_id
    session = get_session(ctx)
    raw_patterns = session.get("patterns", [])
    models = session.get("models", {})

    if not raw_patterns and not models:
        return "Nothing to visualize. Load a model with cml2graph_load_model or run cminer_mine_patterns first."

    # Resolve a plugin for pattern rendering (use first available model's plugin)
    plugin_name = None
    for entry in models.values():
        plugin_name = entry.get("plugin_name")
        if plugin_name:
            break
    if plugin_name is None:
        plugin_name = "ontouml"

    cml2graph_plugin = (
        _plugin_manager.get(plugin_name)
        if plugin_name in _plugin_manager.available
        else _plugin_manager.get("ontouml")
    )

    # Build pattern PlantUML strings
    patterns_def: list[PatternInfo] = []
    plantuml_patterns: list[str] = []

    if raw_patterns:
        patterns_def = [
            PatternInfo(
                id=p["id"],
                nodes=[tuple(n) for n in p["nodes"]],
                edges=[tuple(e) for e in p["edges"]],
                support=p["support"],
                frequency=p["frequency"],
            )
            for p in raw_patterns
        ]
        gspan_format = GSpanFormat()
        for p in patterns_def:
            gspan_text   = pattern_to_tve_gdata(p)
            gspan_graphs = gspan_format.parse(gspan_text)
            for g in gspan_graphs:
                g = cml2graph_plugin.enrich_pattern(g)
                plantuml_patterns.append(cml2graph_plugin.render(g))

    # Render each model's current_graph to PlantUML
    models_plantuml: dict[str, str] = {}
    for name, entry in models.items():
        graph = entry["current_graph"]
        p_name = entry.get("plugin_name", plugin_name)
        plug = (
            _plugin_manager.get(p_name)
            if p_name in _plugin_manager.available
            else cml2graph_plugin
        )
        try:
            models_plantuml[name] = plug.render(graph, "plantuml")
        except Exception as e:
            logger.warning(f"Could not render model '{name}': {e}")

    # Build and store HTML
    html = generate_html_visualization(
        patterns_def,
        plantuml_patterns,
        models_plantuml=models_plantuml,
    )
    session["html"] = html
    logger.debug(f"Session {session_id}: generated visualization HTML (length={len(html)})")

    parts = []
    if patterns_def:
        parts.append(f"{len(patterns_def)} pattern(s)")
    if models_plantuml:
        parts.append(f"{len(models_plantuml)} model(s)")

    return (
        f"Visualization generated: {', '.join(parts)}.\n"
        f"Visualization resource URI: cminer://sessions/{session_id}/private/visualization"
    )


# ====================== Pattern query tools ======================

@mcp.tool()
async def cminer_get_pattern_info(
    pattern_id: Annotated[int, "The numeric ID of the pattern to inspect."],
    ctx: Context = None,
) -> str:
    """
    Use this to get details for a specific pattern by its ID:
    support, frequency, node list, edge list.
    Use this whenever the user asks about a specific pattern number.
    """
    if not ctx or not ctx.session_id:
        return "Error: No session ID in MCP context."

    session = SESSIONS.get(ctx.session_id)
    if session is None:
        return "Error: No patterns in this session. Run cminer_run first."

    patterns = session.get("patterns", [])
    for p in patterns:
        if p["id"] == pattern_id:
            node_lines = [f"  v {nid} {lbl}" for nid, lbl in p["nodes"]]
            edge_lines = [f"  e {src} {dst} {lbl}" for src, dst, lbl in p["edges"]]
            return (
                f"Pattern #{p['id']}\n"
                f"  Support   : {p['support']}\n"
                f"  Frequency : {p['frequency']}\n"
                f"  Nodes ({len(p['nodes'])}):\n" + ("\n".join(node_lines) or "    (none)") + "\n"
                f"  Edges ({len(p['edges'])}):\n" + ("\n".join(edge_lines) or "    (none)")
            )

    available_ids = sorted(p["id"] for p in patterns)
    return (
        f"Pattern #{pattern_id} not found.\n"
        f"Available pattern IDs: {available_ids[:50]}"
        + ("  …" if len(available_ids) > 50 else "")
    )

@mcp.tool()
async def cminer_filter_patterns(
    min_frequency: Annotated[int | None, "Minimum frequency (inclusive)."] = None,
    max_frequency: Annotated[int | None, "Maximum frequency (inclusive)."] = None,
    min_edges: Annotated[int | None, "Minimum edge count (inclusive)."] = None,
    max_edges: Annotated[int | None, "Maximum edge count (inclusive)."] = None,
    min_nodes: Annotated[int | None, "Minimum node count (inclusive)."] = None,
    max_nodes: Annotated[int | None, "Maximum node count (inclusive)."] = None,
    min_support: Annotated[float | None, "Minimum support value (inclusive)."] = None,
    ctx: Context = None,
) -> str:
    """
    Use this to filter mined patterns by support, frequency, node count, or edge count.
    Returns matching pattern IDs with their key metrics.
    Use this when the user asks to find patterns matching certain criteria.
    """
    if not ctx or not ctx.session_id:
        return "Error: No session ID in MCP context."

    session = SESSIONS.get(ctx.session_id)
    if session is None:
        return "Error: No patterns in this session. Run cminer_run first."

    patterns = session.get("patterns", [])
    matched  = []
    for p in patterns:
        n_nodes = len(p["nodes"])
        n_edges = len(p["edges"])
        if min_frequency is not None and p["frequency"] < min_frequency: continue
        if max_frequency is not None and p["frequency"] > max_frequency: continue
        if min_edges is not None and n_edges < min_edges: continue
        if max_edges is not None and n_edges > max_edges: continue
        if min_nodes is not None and n_nodes < min_nodes: continue
        if max_nodes is not None and n_nodes > max_nodes: continue
        if min_support is not None and p["support"] < min_support: continue
        matched.append(p)

    if not matched:
        return "No patterns match the given filters."

    lines = [f"Found {len(matched)} matching pattern(s):"]
    for p in matched[:100]:
        lines.append(
            f"  #{p['id']:>4}  support={p['support']}  freq={p['frequency']:>3}"
            f"  nodes={len(p['nodes'])}  edges={len(p['edges'])}"
        )
    if len(matched) > 100:
        lines.append(f"  … and {len(matched) - 100} more. Refine your filters to narrow results.")
    return "\n".join(lines)


@mcp.tool()
async def cminer_get_session_summary(ctx: Context = None) -> str:
    """
    Use this to get a quick statistical summary of the patterns mined in this session:
    total count, support range, frequency range, edge count distribution.
    """
    if not ctx or not ctx.session_id:
        return "Error: No session ID in MCP context."

    session = SESSIONS.get(ctx.session_id)
    if session is None:
        return "No patterns in this session. Run cminer_run first."

    patterns = session.get("patterns", [])
    if not patterns:
        return "Session exists but contains 0 patterns."

    supports    = [p["support"]      for p in patterns]
    frequencies = [p["frequency"]    for p in patterns]
    edge_counts = [len(p["edges"])   for p in patterns]
    node_counts = [len(p["nodes"])   for p in patterns]

    freq_dist  = Counter(frequencies)
    edge_dist  = Counter(edge_counts)

    top_freq   = freq_dist.most_common(5)
    top_edges  = sorted(edge_dist.items())[:10]

    return (
        f"Session summary — {len(patterns)} patterns\n"
        f"  Support    : min={min(supports):.3f}  max={max(supports):.3f}\n"
        f"  Frequency  : min={min(frequencies)}  max={max(frequencies)}  "
        f"avg={sum(frequencies)/len(frequencies):.1f}\n"
        f"  Nodes      : min={min(node_counts)}  max={max(node_counts)}\n"
        f"  Edges      : min={min(edge_counts)}  max={max(edge_counts)}\n"
        f"  Top frequencies : {top_freq}\n"
        f"  Edge count dist : {top_edges}"
    )

