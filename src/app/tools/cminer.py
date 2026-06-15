import asyncio
import contextlib
import copy
import io
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from datetime import datetime

from typing import Annotated
from fastmcp import FastMCP, Context
from fastmcp.server.lifespan import lifespan
from fastmcp.server.tasks import TaskConfig
from fastmcp.server.dependencies import get_access_token
from fastmcp.dependencies import CurrentContext

from CMiner import CMinerAPI

from cml2graph.core.formats.gspan_fmt import GSpanFormat
from cml2graph.core.formats.node_link_fmt import NodeLinkFormat


from app.utils.logger import setup_logging
from app.core.state import (
    SESSIONS,
    plugin_manager as _plugin_manager,
    get_session,
    session_store,
    set_focus,
)
from app.utils.cminer_patterns_helper import (
    PatternInfo,
    parse_cminer_output,
    pattern_to_tve_gdata,
    resolve_occurrence_highlights,
)
from app.utils.plantuml_helper import plantuml_encode

logger = setup_logging("DEBUG", __name__)


@lifespan
async def _cminer_lifespan(server):
    cminer = CMinerAPI(
        db_file=None,
        support=1,
        max_nodes=10,
        string_output=True,
        show_mappings=True,
        directed_graph=True,
        with_frequencies=True,
    )
    try:
        yield {"cminer": cminer}
    finally:
        cminer.close()


mcp = FastMCP("cminer", lifespan=_cminer_lifespan)


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
    """
    Use this to set the minimum support threshold for the pattern mining through CMiner.
    support definition: The number of unique graphs in the dataset that contain at least one instance of the subgraph
    """
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

    client_id = get_access_token().client_id or "anonymous"
    session_id = ctx.session_id
    cminer     = ctx.lifespan_context["cminer"]
    session = get_session(ctx)

    models = session.get("models", {})
    tmp_files: list[str] = []
    nlf = NodeLinkFormat()

    # check if db file is set
    if not models and not cminer.db_file:
        return "Error: No models loaded and no db_file configured for CMiner. Load one or more models before mining."
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
        # then load them all into CMiner's db.
        # Use the model name as the filename so CMiner's mapping output
        # contains human-readable names instead of random temp strings.
        tmp_dir = tempfile.mkdtemp(prefix="cminer_batch_")
        for name, entry in models.items():
            graph = entry["current_graph"]
            json_text = nlf.render(graph)
            safe_name = "".join(
                c if c.isalnum() or c in ("_", "-") else "_" for c in name
            )
            tmp_path = os.path.join(tmp_dir, f"{safe_name}.json")
            # Handle rare collision after sanitization
            counter = 1
            original_safe = safe_name
            while os.path.exists(tmp_path):
                safe_name = f"{original_safe}_{counter}"
                tmp_path = os.path.join(tmp_dir, f"{safe_name}.json")
                counter += 1
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(json_text)
            tmp_files.append(tmp_path)
            logger.debug(
                f"Session {session_id}: model '{name}' — "
                f"{graph.number_of_nodes()} nodes, "
                f"{graph.number_of_edges()} edges -> {tmp_path}"
            )
            # Load this file into CMiner's db (accumulate)
            cminer.set_db_file(tmp_path)
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
            if 'tmp_dir' in locals():
                shutil.rmtree(tmp_dir, ignore_errors=True)

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

    # If show_mappings is enabled we take the structured pattern info
    if cminer.show_mappings:
        structured = cminer.get_structured_patterns()
        for idx, p in enumerate(structured, start=1):
            p["id"] = idx
        patterns = structured
        logger.debug(f"Using structured pattern data: {len(patterns)} patterns")
    else:
        patterns = parse_cminer_output(output_text)
        patterns = [p.__dict__ for p in patterns]
        logger.debug(f"Fallback to string parsing: {len(patterns)} patterns")

    session["patterns"] = patterns
    session["mine_parameters"] = {
        "min_support": cminer.min_support,
        "min_nodes": cminer.stack.min_nodes,
        "max_nodes": cminer.stack.max_nodes,
    }
    session["viz_data"] = None
    # Invalidate highlight cache, old renders reference pattern occurrences
    # from the previous mining run and are no longer valid.
    session["highlights"] = {}

    # Focus the viewer on the patterns tab next time it opens (first pattern
    # is auto-selected by the viewer when pattern_id is not set).
    set_focus(session, tab="patterns", pattern_id=None, highlight=None)

    # Auto-save TODO: make this async and non-blocking
    try:
        session_store.save_session(
            client_id=client_id,
            session_id=ctx.session_id,
            session_data=session,
            name=f"Auto-save {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            description=f"Automatically saved after mining {len(patterns)} patterns"
        )
        logger.info(f"Session {ctx.session_id} auto-saved")
    except Exception as e:
        logger.warning(f"Could not auto-save session: {e}")


    await ctx.info(f"Session {session_id}: {len(patterns)} patterns stored.")
    return (
        f"CMiner completed. Found {len(patterns)} pattern(s).\n"
        f"Pattern data resource URI: cminer://sessions/{session_id}/patterns\n"
        f"Note: call cminer_generate_visualization to update the visualization."
    )


@mcp.tool()
async def cminer_generate_visualization(ctx: Context = CurrentContext()) -> str:
    """
    Generate an interactive HTML visualization.
    - If patterns have been mined, shows the pattern browser.
    - If models are loaded, shows a Models tab with a clickable list of all models.
    - Both tabs are shown together when both are available.
    """
    logger.debug(f"Client ID {get_access_token().client_id}: generating visualization...")
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

    # Build and store visualization data (encoded strings + metadata)
    viz_patterns = []
    for idx, p in enumerate(patterns_def):
        viz_patterns.append({
            "id": p.id,
            "support": p.support,
            "frequency": p.frequency,
            "nodes": len(p.nodes),
            "edges": len(p.edges),
            "encoded": plantuml_encode(plantuml_patterns[idx]),
        })

    viz_models = []
    for name, puml in models_plantuml.items():
        viz_models.append({
            "name": name,
            "encoded": plantuml_encode(puml),
        })

    session["viz_data"] = {
        "patterns": viz_patterns,
        "models": viz_models,
        "version": int(__import__("time").time() * 1000),
    }
    logger.debug(f"Session {session_id}: stored viz_data ({len(viz_patterns)} patterns, {len(viz_models)} models)")

    parts = []
    if patterns_def:
        parts.append(f"{len(patterns_def)} pattern(s)")
    if models_plantuml:
        parts.append(f"{len(models_plantuml)} model(s)")

    return (
        f"Visualization generated: {', '.join(parts)}.\n"
        f"View it at: /visualization/{session_id}\n"
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
            # Focus viewer on this pattern next time it opens
            set_focus(session, tab="patterns", pattern_id=pattern_id)
            node_lines = [f"  v {nid} {' '.join(lbl)}" for nid, lbl in p["nodes"]]
            edge_lines = [f"  e {src} {dst} {lbl}" for src, dst, lbl in p["edges"]]
            lines = [
                f"Pattern #{p['id']}",
                f"  Support   : {p['support']}",
                f"  Frequency : {p['frequency']}",
                f"  Nodes ({len(p['nodes'])}):",
                "\n".join(node_lines) or "    (none)",
                f"  Edges ({len(p['edges'])}):",
                "\n".join(edge_lines) or "    (none)",
            ]
            mappings = p.get("mappings")
            if mappings:
                lines.append("  Mappings:")
                if isinstance(mappings, dict):
                    for graph_name, occurrences in mappings.items():
                        lines.append(f"    {graph_name}: {len(occurrences)} occurrence(s)")
                        for occ_idx, occ in enumerate(occurrences, 1):
                            node_map = occ.get("nodes", {})
                            edge_list = occ.get("edges", [])
                            lines.append(f"      Occurrence {occ_idx}:")
                            lines.append(f"        Nodes: {node_map}")
                            lines.append(f"        Edges: {edge_list}")
                else:
                    lines.append("    " + str(mappings).replace("\n", "\n    "))
            return "\n".join(lines)

    available_ids = sorted(p["id"] for p in patterns)
    return (
        f"Pattern #{pattern_id} not found.\n"
        f"Available pattern IDs: {available_ids[:50]}"
        + ("  …" if len(available_ids) > 50 else "")
    )


@mcp.tool()
async def cminer_highlight_pattern(
    pattern_id: Annotated[int, "The numeric ID of the pattern to highlight."],
    model_name: Annotated[str, "Name of the loaded model to highlight the pattern in."],
    occurrence_index: Annotated[int | None, "0-based index of a specific occurrence to highlight. If omitted, all occurrences are highlighted."] = None,
    ctx: Context = None,
) -> str:
    """
    Highlight occurrences of a mined pattern inside a specific loaded model.
    Returns the IDs of highlighted nodes/edges and an encoded PlantUML image.
    """
    if not ctx or not ctx.session_id:
        return "Error: No session ID in MCP context."

    session = get_session(ctx)
    patterns = session.get("patterns", [])
    models = session.get("models", {})

    # Find the pattern
    pattern = None
    for p in patterns:
        if p["id"] == pattern_id:
            pattern = p
            break
    if pattern is None:
        available = sorted(pp["id"] for pp in patterns)
        return f"Pattern #{pattern_id} not found. Available: {available[:50]}"

    # Find the model
    if model_name not in models:
        available_models = ", ".join(models.keys()) or "(none)"
        return f"Model '{model_name}' not found. Loaded: {available_models}"

    entry = models[model_name]
    graph = copy.deepcopy(entry["current_graph"])
    plugin_name = entry.get("plugin_name", "ontouml")
    plugin = (
        _plugin_manager.get(plugin_name)
        if plugin_name in _plugin_manager.available
        else _plugin_manager.get("ontouml")
    )

    # Get structured mappings
    mappings = pattern.get("mappings")
    if not mappings or not isinstance(mappings, dict):
        return (
            f"Pattern #{pattern_id} has no structured mappings. "
            f"Re-run mining with the patched CMiner to get mapping data."
        )

    # Find occurrences for this model.
    # Model names in mappings have the .json suffix stripped by CMiner.
    # We need to match against the sanitized name used during mining.
    target_graph_name = model_name
    occurrences = None
    for graph_name, occs in mappings.items():
        # CMiner uses the filename without extension as graph name
        if graph_name == target_graph_name or graph_name == target_graph_name.replace(".json", ""):
            occurrences = occs
            break

    if occurrences is None:
        # Try with sanitized name (same logic as mining)
        safe_name = "".join(
            c if c.isalnum() or c in ("_", "-") else "_" for c in model_name
        )
        for graph_name, occs in mappings.items():
            if graph_name == safe_name:
                occurrences = occs
                break

    if not occurrences:
        return f"Pattern #{pattern_id} was not found in model '{model_name}'."

    # Select occurrence(s)
    if occurrence_index is not None:
        if occurrence_index < 0 or occurrence_index >= len(occurrences):
            return (
                f"Occurrence {occurrence_index} out of range. "
                f"Model '{model_name}' has {len(occurrences)} occurrence(s)."
            )
        occurrences = [occurrences[occurrence_index]]

    # Collect highlighted nodes/edges from occurrences.
    # CMiner uses integer edge keys internally; current_graph has UUID keys.
    # resolve_occurrence_highlights handles that mismatch.
    highlighted_nodes, highlighted_edges = resolve_occurrence_highlights(occurrences, graph)

    # Apply highlight attributes to the graph copy
    for node in graph.nodes():
        if node in highlighted_nodes:
            graph.nodes[node]["highlight"] = True
            graph.nodes[node]["highlight_line_color"] = "#E84A4A"
    for src, dst, key in graph.edges(keys=True):
        if (src, dst, key) in highlighted_edges:
            graph.edges[src, dst, key]["highlight"] = True
            graph.edges[src, dst, key]["highlight_line_color"] = "#E84A4A"

    # Render highlighted graph
    try:
        plantuml_text = plugin.render(graph, "plantuml")
        print(f"Rendered highlighted model: {plantuml_text}")
        encoded = plantuml_encode(plantuml_text)
    except Exception as e:
        logger.warning(f"Could not render highlighted model: {e}")
        return f"Error rendering highlighted model: {e}"

    # Store in session — key includes occurrence_index so all-occurrences and
    # per-occurrence renders are cached independently and never overwrite each other.
    if "highlights" not in session:
        session["highlights"] = {}
    cache_key = f"{pattern_id}_{model_name}_{occurrence_index}"
    session["highlights"][cache_key] = {
        "pattern_id": pattern_id,
        "model_name": model_name,
        "occurrence_index": occurrence_index,
        "highlighted_nodes": list(highlighted_nodes),
        "highlighted_edges": [
            {"src": s, "dst": d, "key": k} for s, d, k in highlighted_edges
        ],
        "encoded": encoded,
    }

    # Focus the viewer: models tab with the highlight applied. Also set
    # pattern_id so that if the user manually switches to the patterns tab,
    # the highlighted pattern is already selected there too.
    set_focus(
        session,
        tab="models",
        pattern_id=pattern_id,
        model_name=model_name,
        highlight={
            "pattern_id": pattern_id,
            "model_name": model_name,
            "occurrence_index": occurrence_index,
        },
    )

    node_list = ", ".join(str(n) for n in sorted(highlighted_nodes))
    edge_list = ", ".join(f"({s},{d},{k})" for s, d, k in sorted(highlighted_edges))
    occ_desc = (
        f"occurrence {occurrence_index}"
        if occurrence_index is not None
        else f"all {len(occurrences)} occurrence(s)"
    )

    return (
        f"Highlighted pattern #{pattern_id} in '{model_name}' ({occ_desc}).\n"
        f"  Nodes: {node_list}\n"
        f"  Edges: {edge_list}\n"
        f"Note: reload the visualization to see the updated highlight."
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
    # use a copy of the patterns without mappings
    clean_patterns = [
        {
            "id": p["id"],
            "nodes": p["nodes"],
            "edges": p["edges"],
            "support": p["support"],
            "frequency": p["frequency"],
        }
        for p in patterns
    ]
    matched  = []
    for p in clean_patterns:
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

    # Save in the session the last filter criteria and results.
    session["patterns_filters_history"] = session.get("patterns_filters_history", [])
    session["patterns_filters_history"].append({
        "criteria": {
            "min_frequency": min_frequency,
            "max_frequency": max_frequency,
            "min_edges": min_edges,
            "max_edges": max_edges,
            "min_nodes": min_nodes,
            "max_nodes": max_nodes,
            "min_support": min_support,
        },
        "results": matched,
    })

    try:
        session_store.additional_save_session(
            client_id=get_access_token().client_id or "anonymous",
            session_id=ctx.session_id,
            session_data=session,
            name=f"Filter patterns {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            description=f"Filtered patterns criteria"
        )
        logger.info(f"Session {ctx.session_id} filter criteria auto-saved")
    except Exception as e:
        logger.warning(f"Could not auto-save session after filtering patterns: {e}")

    # Focus viewer on the patterns tab, selecting the first match
    set_focus(session, tab="patterns", pattern_id=matched[0]["id"], highlight=None)

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

