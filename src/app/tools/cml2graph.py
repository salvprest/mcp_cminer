import copy
import json
import os
import tempfile
from pathlib import Path

from typing import Annotated
from fastmcp import FastMCP, Context
from cml2graph.core.prompts import summarize_graph
from cml2graph.core.formats.node_link_fmt import NodeLinkFormat

from app.core.state import (
    UPLOAD_DIR,
    PARSED_MODELS,
    plugin_manager as _plugin_manager,
    get_session,
    get_active_plugin,
)

mcp = FastMCP("cml2graph")


@mcp.tool()
async def cml2graph_load_model(
    file_name: Annotated[str, "model graph file name (e.g., 'model.json'). The file will be loaded into your session so you can filter, explain, and render it."],
    ctx: Context = None,
) -> str:
    """
    Use this to load an uploaded graph model file (by name) into the current session.
    After loading, filter/explain/render tools become functional.
    """
    session = get_session(ctx)

    # If a full path was passed, use only the filename component
    file_name = Path(file_name).name

    # Resolve the absolute path server-side
    file_path = None
    for f in UPLOAD_DIR.rglob(file_name):
        if f.is_file():
            file_path = str(f)
            break
    if not file_path:
         return f"File '{file_name}' not found on the server."

    # Try the in-memory cache first (populated at upload time)
    model_info = PARSED_MODELS.get(file_path)

    if model_info is not None:
        graph = model_info["graph"]
        plugin_name = model_info["plugin_name"]
    else:
        # Fallback: try to parse the file on demand
        if not Path(file_path).is_file():
            return f"File not found: {file_path}"

        text = Path(file_path).read_text(encoding="utf-8")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return f"File is not valid JSON: {file_path}"

        # Try each plugin
        plugin_name = None
        graph = None
        for p_name in _plugin_manager.available:
            plugin = _plugin_manager.get(p_name)
            if plugin.is_valid(data):
                tmp = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False, encoding="utf-8"
                )
                json.dump(data, tmp, ensure_ascii=False)
                tmp.close()
                try:
                    graph = plugin.parse(tmp.name)
                finally:
                    os.unlink(tmp.name)
                plugin_name = p_name
                break

        # Maybe it's already node-link format
        if graph is None:
            try:
                nlf = NodeLinkFormat()
                graphs = nlf.parse(text)
                if graphs:
                    graph = graphs[0]
                    plugin_name = Path(file_path).parent.name
                    if plugin_name not in _plugin_manager.available:
                        plugin_name = _plugin_manager.available[0] if _plugin_manager.available else None
            except Exception:
                pass

        if graph is None:
            return f"No plugin can handle: {file_path}"

        # Cache for next time
        PARSED_MODELS[file_path] = {
            "graph": graph,
            "plugin_name": plugin_name,
        }

    # Store in session (deep copy so filters don't corrupt the cache)
    session["active_model"] = file_path
    session["original_graph"] = copy.deepcopy(graph)
    session["current_graph"] = copy.deepcopy(graph)
    session["plugin_name"] = plugin_name

    summary = summarize_graph(graph)
    return (
        f"Loaded '{Path(file_path).name}' model/graph as {plugin_name}.\n\n"
        f"{summary}\n\n"
        f"Use cml2graph_list_filters to see available filters.\n"
        f"Use cml2graph_explain_model for a detailed analysis prompt."
    )


@mcp.tool()
async def cml2graph_list_filters(ctx: Context = None) -> str:
    """
    Use this to list all available filters for the loaded model graph, with
    descriptions, parameter names, and valid values (enums).
    Call this before using cml2graph_filter.
    """
    session = get_session(ctx)
    plugin = get_active_plugin(session)
    if plugin is None:
        return "No model loaded. Call cml2graph_load_model first."

    tool_meta = plugin.get_tool_meta()

    lines = [f"Filters for {session['plugin_name']}:\n"]
    for fname, fmeta in tool_meta.items():
        lines.append(f"── {fname} ──")
        lines.append(f"  {fmeta['description']}")
        params = fmeta.get("params", {})
        if params:
            lines.append("  Parameters:")
            for pname, pinfo in params.items():
                required = pinfo.get("required", True)
                req_tag = "" if required else " (optional)"
                lines.append(f"    {pname}{req_tag}: {pinfo['description']}")
                enum = pinfo.get("enum")
                if enum:
                    lines.append(f"      Valid values: {', '.join(str(v) for v in enum)}")
        else:
            lines.append("  No parameters.")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def cml2graph_filter(
    filter_name: Annotated[str, "Name of the filter to apply. Call cml2graph_list_filters first to see available names."],
    arguments_json: Annotated[str, "JSON object with filter arguments. Example: {'stereotypes': ['kind', 'role']}. Call cml2graph_list_filters to see valid argument names and values."] = "{}",
    ctx: Context = None,
) -> str:
    """
    Use this to apply a filter to the currently loaded model graph.

    The graph is modified in-place for the session. To undo,
    call cml2graph_reset_model.
    Filters can be chained — each one operates on the result of the previous.
    """
    session = get_session(ctx)
    plugin = get_active_plugin(session)
    if plugin is None:
        return "No model loaded. Call cml2graph_load_model first."

    graph = session.get("current_graph")
    if graph is None:
        return "No model loaded. Call cml2graph_load_model first."

    filters = plugin.get_filters()
    if filter_name not in filters:
        available = ", ".join(filters.keys())
        return f"Unknown filter '{filter_name}'. Available: {available}"

    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON in arguments_json: {e}"

    tool_meta = plugin.get_tool_meta()
    if filter_name in tool_meta:
        valid_params = set(tool_meta[filter_name].get("params", {}).keys())
        unknown = set(args.keys()) - valid_params
        if unknown and valid_params:
            return (
                f"Unknown arguments {unknown} for filter '{filter_name}'. "
                f"Valid: {valid_params}"
            )

    try:
        result = filters[filter_name](graph, **args)
    except TypeError as e:
        return f"Filter argument error: {e}"
    except Exception as e:
        return f"Filter failed: {e}"

    session["current_graph"] = result
    summary = summarize_graph(result)
    return f"Filter '{filter_name}' applied.\n\n{summary}"


@mcp.tool()
async def cml2graph_explain_model(ctx: Context = None) -> str:
    """
    Use this to get a detailed analysis and information about the loaded model graph.
    It give info about the nodes, edges, type counts for the currently loaded model.
    It also give info about what elements, relationships, and patterns it contains, grounded in the
    metamodel rules of the detected language.
    To elaborate a complexive information about the graph model.
    """
    session = get_session(ctx)
    plugin = get_active_plugin(session)
    if plugin is None:
        return "No model loaded. Call cml2graph_load_model first."

    graph = session.get("current_graph")
    if graph is None:
        return "No model loaded. Call cml2graph_load_model first."

    summary_structure_string =summarize_graph(graph)
    general_string = plugin.get_general_prompt(graph)
    return f"structure:{summary_structure_string}\n\ninformation:{general_string}"


@mcp.tool()
async def cml2graph_reset_model(ctx: Context = None) -> str:
    """Use this to reset the model to its original state, undoing all applied filters."""
    session = get_session(ctx)
    original = session.get("original_graph")
    if original is None:
        return "No model loaded."

    session["current_graph"] = copy.deepcopy(original)
    summary = summarize_graph(session["current_graph"])
    return f"Model reset to original state.\n\n{summary}"
