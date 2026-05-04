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
            "html": None,
        }
    return SESSIONS[sid]


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
