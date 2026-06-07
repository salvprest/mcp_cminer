from collections import OrderedDict
from pathlib import Path
from typing import Any

from fastmcp import Context
from cml2graph.core.plugin_manager import PluginManager
from app.core.session_store import SessionStore

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
# Model graph cache: Key = absolute file path (str), value = {"graph": G, "plugin_name": str}
PARSED_MODELS: dict[str, dict[str, Any]] = {}
# Dataset metadata cache: Key = dataset ZIP path (str), value = DatasetInfo
DATASETS: dict[str, dict] = {}
# Per-session mcp state: Key = session_id
SESSIONS: dict[str, dict] = {}
# Singleton plugin manager
plugin_manager = PluginManager()
# Singleton session store
session_store = SessionStore(DATA_DIR / "saved_sessions")


def _empty_focus() -> dict[str, Any]:
    """Default visualization focus, see Focus Protocol in visualization.py."""
    return {
        "tab": None,            # "patterns" | "models" | None
        "pattern_id": None,     # int | None
        "model_name": None,     # str  | None
        "highlight": None,      # None | {"pattern_id": int, "model_name": str, "occurrence_index": int|None}
    }


def get_session(ctx: Context) -> dict[str, Any]:
    """Get or create the session dict for this MCP connection."""
    sid = ctx.session_id
    if sid not in SESSIONS:
        SESSIONS[sid] = {
            # Ordered dict of loaded models: key=file_name, value=model_entry
            # Each model_entry: {"file_path": str, "original_graph": G, "current_graph": G, "plugin_name": str}
            "models": OrderedDict(),
            "active_model": None,    # key into models dict (file_name)
            "patterns": [],
            # Pre-computed visualization data for the API
            # {"patterns": [{"id","support","frequency","nodes","edges","encoded"},...],
            #  "models":   [{"name","encoded"},...]}
            "viz_data": None,
            # Visualization focus, see Focus Protocol in app/core/visualization.py
            "focus": _empty_focus(),
        }
    # Backfill focus for sessions restored from disk that predate this field
    if "focus" not in SESSIONS[sid]:
        SESSIONS[sid]["focus"] = _empty_focus()
    return SESSIONS[sid]


_UNSET = object()


def set_focus(
    session: dict,
    *,
    tab: Any = _UNSET,
    pattern_id: Any = _UNSET,
    model_name: Any = _UNSET,
    highlight: Any = None,
) -> None:
    """
    Update the visualization focus for this session.

    `tab`, `pattern_id` and `model_name` are merged: only fields explicitly
    passed are modified; pass `None` to explicitly clear them.

    `highlight` is different, it is **cleared by default** on every call.
    Only tools that actively want to set a highlight (currently only
    `cminer_highlight_pattern`) need to pass it. Any other tool action
    implicitly removes the highlight overlay.

    See Focus Protocol in app/core/visualization.py for semantics.
    """
    focus = session.setdefault("focus", _empty_focus())
    if tab is not _UNSET:
        focus["tab"] = tab
    if pattern_id is not _UNSET:
        focus["pattern_id"] = pattern_id
    if model_name is not _UNSET:
        focus["model_name"] = model_name
    focus["highlight"] = highlight


def get_active_model_entry(session: dict) -> dict | None:
    """Return the model entry dict for the currently active model, or None."""
    name = session.get("active_model")
    if name is None:
        return None
    return session["models"].get(name)


def get_active_plugin(session: dict) -> Any | None:
    """Resolve the plugin object from the active model's plugin_name."""
    entry = get_active_model_entry(session)
    if entry is None:
        return None
    name = entry.get("plugin_name")
    if name is None:
        return None
    return plugin_manager.get(name)
