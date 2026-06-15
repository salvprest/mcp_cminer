import copy
import json
import os
import tempfile
from pathlib import Path

from typing import Annotated
from fastmcp import FastMCP, Context
from cml2graph.core.prompts import summarize_graph
from cml2graph.core.formats.node_link_fmt import NodeLinkFormat
from app.utils.logger import setup_logging

logger = setup_logging("DEBUG", __name__)

from app.core.state import (
    UPLOAD_DIR,
    PARSED_MODELS,
    plugin_manager as _plugin_manager,
    get_session,
    get_active_model_entry,
    get_active_plugin,
    set_focus,
)

mcp = FastMCP("cml2graph")


# ── helpers ──

def _resolve_and_parse(file_name: str):
    """Resolve a file name to an absolute path and parse it into a graph.
    Returns (file_path, graph, plugin_name) or raises ValueError."""
    file_name = Path(file_name).name

    file_path = None
    for f in UPLOAD_DIR.rglob(file_name):
        if f.is_file():
            file_path = str(f)
            break
    if not file_path:
        raise ValueError(f"File '{file_name}' not found on the server.")

    model_info = PARSED_MODELS.get(file_path)
    if model_info is not None:
        return file_path, model_info["graph"], model_info["plugin_name"]

    if not Path(file_path).is_file():
        raise ValueError(f"File not found: {file_path}")

    text = Path(file_path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError(f"File is not valid JSON: {file_path}")

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
        raise ValueError(f"No plugin can handle: {file_path}")

    PARSED_MODELS[file_path] = {"graph": graph, "plugin_name": plugin_name}
    return file_path, graph, plugin_name


# ── tools ──

@mcp.tool()
async def cml2graph_load_model(
    file_name: Annotated[str, "model graph file name (e.g., 'model.json'). The file will be loaded into your session so you can filter, explain, and render it."],
    ctx: Context = None,
) -> str:
    """
    Use this to load an uploaded graph model into the session model list and make it active.
    If the model is already in the list, it is simply activated.
    After loading, filter/explain/render tools work on this model.
    """
    session = get_session(ctx)
    key = Path(file_name).name

    # If already loaded, just activate it
    if key in session["models"]:
        session["active_model"] = key
        entry = session["models"][key]
        summary = summarize_graph(entry["current_graph"])
        set_focus(session, tab="models", model_name=key, highlight=None)
        return (
            f"Model '{key}' is already loaded — now active.\n\n"
            f"{summary}\n\n"
            f"Use cml2graph_list_filters to see available filters and see parameter details."
        )

    try:
        file_path, graph, plugin_name = _resolve_and_parse(file_name)
    except ValueError as e:
        return str(e)

    session["models"][key] = {
        "file_path": file_path,
        "original_graph": copy.deepcopy(graph),
        "current_graph": copy.deepcopy(graph),
        "plugin_name": plugin_name,
    }
    session["active_model"] = key
    set_focus(session, tab="models", model_name=key, highlight=None)

    summary = summarize_graph(graph)
    total = len(session["models"])
    return (
        f"Loaded '{key}' as {plugin_name} (model {total} in list, now active).\n\n"
        f"{summary}\n\n"
        f"Use cml2graph_list_filters to see available filters and their parameter details.\n"
        f"Use cml2graph_explain_model for a detailed analysis prompt.\n"
        f"Use cml2graph_list_models to see all loaded models.\n"
        f"Note: call cminer_generate_visualization to update the visualization."
    )


@mcp.tool()
async def cml2graph_list_models(ctx: Context = None) -> str:
    """
    Use this to list all models currently loaded in the session with their status
    (node/edge counts, filtered or not, which is active).
    """
    session = get_session(ctx)
    models = session.get("models", {})
    if not models:
        return "No models loaded. Call cml2graph_load_model first."

    active = session.get("active_model")
    lines = [f"Loaded models ({len(models)}):"]
    for idx, (name, entry) in enumerate(models.items(), 1):
        g = entry["current_graph"]
        o = entry["original_graph"]
        filtered = (g.number_of_nodes() != o.number_of_nodes()
                     or g.number_of_edges() != o.number_of_edges())
        marker = " ◀ active" if name == active else ""
        filt_tag = " [filtered]" if filtered else ""
        lines.append(
            f"  {idx}. {name} — {g.number_of_nodes()}N / {g.number_of_edges()}E"
            f"  ({entry['plugin_name']}){filt_tag}{marker}"
        )
    return "\n".join(lines)


@mcp.tool()
async def cml2graph_select_model(
    file_name: Annotated[str, "Name of a model already in the list to make active."],
    ctx: Context = None,
) -> str:
    """Use this to switch the active model to one already loaded in the list."""
    session = get_session(ctx)
    key = Path(file_name).name
    if key not in session["models"]:
        available = ", ".join(session["models"].keys()) or "(none)"
        return f"Model '{key}' not in list. Available: {available}"

    session["active_model"] = key
    entry = session["models"][key]
    summary = summarize_graph(entry["current_graph"])
    set_focus(session, tab="models", model_name=key, highlight=None)
    return f"'{key}' is now the active model.\n\n{summary}"


@mcp.tool()
async def cml2graph_remove_model(
    file_name: Annotated[str, "Name of a model to remove from the list."],
    ctx: Context = None,
) -> str:
    """Use this to remove a model from the session list. If it was active, active becomes None."""
    session = get_session(ctx)
    key = Path(file_name).name
    if key not in session["models"]:
        return f"Model '{key}' not in list."

    del session["models"][key]
    if session["active_model"] == key:
        session["active_model"] = next(iter(session["models"]), None)

    # If the focused/highlighted model was the one removed, clear that focus.
    focus = session.get("focus") or {}
    fkw: dict = {"tab": "models"}
    if focus.get("model_name") == key:
        fkw["model_name"] = session["active_model"]
    h = focus.get("highlight")
    if h and h.get("model_name") == key:
        fkw["highlight"] = None
    set_focus(session, **fkw)

    remaining = len(session["models"])
    active = session["active_model"] or "(none)"
    result = f"Removed '{key}'. {remaining} model(s) remaining. Active: {active}"
    if remaining > 0 or session.get("patterns"):
        result += "\nNote: call cminer_generate_visualization to update the visualization."
    return result


@mcp.tool()
async def cml2graph_clear_models(ctx: Context = None) -> str:
    """Clear all models from the session list and start fresh."""
    session = get_session(ctx)
    session["models"].clear()
    session["active_model"] = None
    set_focus(session, tab="models", model_name=None, highlight=None)
    result = "All models cleared from the list."
    if session.get("patterns"):
        result += "\nNote: call cminer_generate_visualization to update the visualization."
    return result


@mcp.tool()
async def cml2graph_list_filters(ctx: Context = None) -> str:
    """
    Use this to list all available filters for the active model, with
    descriptions, parameter names, and valid values (enums).
    Use it before using cml2graph_filter.
    """
    session = get_session(ctx)
    plugin = get_active_plugin(session)
    if plugin is None:
        return "No model loaded. Call cml2graph_load_model first."

    entry = get_active_model_entry(session)
    tool_meta = plugin.get_tool_meta()

    lines = [f"Filters for {entry['plugin_name']}:\n"]
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
    Use this to apply a filter to the active model.
    Filters can be chained — each one operates on the result of the previous.
    ALWAYS use cml2graph_list_filters to understand how to use the filters.
    To undo, use cml2graph_reset_model.
    """
    session = get_session(ctx)
    entry = get_active_model_entry(session)
    plugin = get_active_plugin(session)
    if entry is None or plugin is None:
        return "No model loaded. Call cml2graph_load_model first."

    graph = entry["current_graph"]

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

    entry["current_graph"] = result
    session["filters_history"] = session.get("filters_history", []) + [{filter_name: args}]
    summary = summarize_graph(result)
    set_focus(session, tab="models", model_name=session["active_model"], highlight=None)
    return (
        f"Filter '{filter_name}' applied to '{session['active_model']}'.\n\n{summary}\n"
        f"Note: call cminer_generate_visualization to update the visualization."
    )


@mcp.tool()
async def cml2graph_explain_model(ctx: Context = None) -> str:
    """
    Use this to get a detailed analysis of the active model: structure, elements,
    relationships, grounded in the metamodel rules of the detected language.
    """
    session = get_session(ctx)
    entry = get_active_model_entry(session)
    plugin = get_active_plugin(session)
    if entry is None or plugin is None:
        return "No model loaded. Call cml2graph_load_model first."

    graph = entry["current_graph"]
    summary_structure_string = summarize_graph(graph)
    general_string = plugin.get_general_prompt(graph)
    return f"structure:{summary_structure_string}\n\ninformation:{general_string}"


@mcp.tool()
async def cml2graph_reset_model(
    file_name: Annotated[str | None, "Name of a model to reset. If omitted, resets the active model."] = None,
    ctx: Context = None,
) -> str:
    """Use this to reset a model to its original state, undoing all applied filters."""
    session = get_session(ctx)
    key = Path(file_name).name if file_name else session.get("active_model")
    if key is None or key not in session["models"]:
        return "Model not found. Call cml2graph_load_model first."

    entry = session["models"][key]
    entry["current_graph"] = copy.deepcopy(entry["original_graph"])
    summary = summarize_graph(entry["current_graph"])
    set_focus(session, tab="models", model_name=key, highlight=None)
    return (
        f"Model '{key}' reset to original state.\n\n{summary}\n"
        f"Note: call cminer_generate_visualization to update the visualization."
    )






@mcp.tool()
async def cml2graph_load_dataset(
    dataset_name: Annotated[str, "Name of the uploaded dataset to load (without .zip extension)"],
    limit: Annotated[int | None, "Maximum number of models to load (default: all)"] = None,
    ctx: Context = None,
) -> str:
    """
    Load all models from an uploaded dataset ZIP file into the session.
    """
    from app.core.state import DATASETS
    if dataset_name not in DATASETS:
        available = ", ".join(DATASETS.keys()) or "(none)"
        return (
            f"Dataset '{dataset_name}' not found. "
            f"Upload a dataset ZIP file first. Available datasets: {available}"
        )

    session = get_session(ctx)
    dataset_data = DATASETS[dataset_name]
    models_to_load = dataset_data["models"]

    #if limit is not None and limit > 0:
    #    models_to_load = models_to_load[:limit]

    loaded_count = 0
    failed_count = 0
    for model_entry in models_to_load:
        model_id = model_entry["model_id"]
        file_path = model_entry["file_path"]
        model_name = model_entry.get("name", model_id)

        # Use model_id as key to avoid conflicts
        key = f"{dataset_name}_{model_id}"
        # Skip if already loaded
        if key in session["models"]:
            continue

        try:
            # Parse the model file
            if not Path(file_path).exists():
                logger.warning(f"Model file not found: {file_path}")
                failed_count += 1
                continue
            # Try to parse using NodeLinkFormat
            text = Path(file_path).read_text(encoding="utf-8")
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                failed_count += 1
                continue

            # Detect plugin
            graph = None
            plugin_name = None
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
                        plugin_name = p_name
                    finally:
                        os.unlink(tmp.name)
                    break

            # Fallback to NodeLinkFormat
            if graph is None:
                try:
                    nlf = NodeLinkFormat()
                    graphs = nlf.parse(text)
                    if graphs:
                        graph = graphs[0]
                        plugin_name = "gspan"
                except Exception:
                    failed_count += 1
                    continue

            if graph is None:
                failed_count += 1
                continue

            # Add to session
            session["models"][key] = {
                "file_path": file_path,
                "original_graph": copy.deepcopy(graph),
                "current_graph": copy.deepcopy(graph),
                "plugin_name": plugin_name,
                "dataset": dataset_name,
                "model_id": model_id,
                "display_name": model_name,
            }
            loaded_count += 1

        except Exception as e:
            logger.exception(f"Failed to load model {model_id}: {e}")
            failed_count += 1

    # Set first loaded model as active if none is active
    if session.get("active_model") is None and session["models"]:
        session["active_model"] = next(iter(session["models"]))

    set_focus(session, tab="models", highlight=None)

    total = len(models_to_load)
    return (
        f"Dataset '{dataset_name}' ({dataset_data['info'].title}):\n"
        f"  Loaded: {loaded_count} model(s)\n"
        f"  Failed: {failed_count} model(s)\n"
        f"  Total in dataset: {len(dataset_data['models'])}\n\n"
        f"All loaded models are ready for mining with cminer_mine_patterns.\n"
    )


@mcp.tool()
async def cml2graph_list_datasets(ctx: Context = None) -> str:
    """List all uploaded datasets available for loading."""
    from app.core.state import DATASETS
    if not DATASETS:
        return "No datasets uploaded. Upload a dataset ZIP file first."

    lines = [f"Available datasets ({len(DATASETS)}):"]
    for name, data in DATASETS.items():
        info = data["info"]
        loaded_in_session = 0
        session = get_session(ctx)
        for model_key in session.get("models", {}).keys():
            if model_key.startswith(f"{name}_"):
                loaded_in_session += 1

        lines.append(
            f"\n  {name}\n"
            f"    Title: {info.title}\n"
            f"    Version: {info.version}\n"
            f"    Models: {len(data['models'])}\n"
            f"    Loaded in session: {loaded_in_session}\n"
            f"    Updated: {info.last_updated}"
        )

    return "\n".join(lines)