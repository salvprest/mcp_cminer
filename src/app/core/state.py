from pathlib import Path
from typing import Any

from fastmcp import Context
from cml2graph.core.plugin_manager import PluginManager

UPLOAD_DIR = Path(__file__).resolve().parents[3] / "uploads"
# Model graph cache: Key = absolute file path (str), value = {"graph": G, "plugin_name": str}
PARSED_MODELS: dict[str, dict[str, Any]] = {}
# Per-session mcp state: Key = session_id
SESSIONS: dict[str, dict] = {}
# Singleton plugin manager
plugin_manager = PluginManager()


def get_session(ctx: Context) -> dict[str, Any]:
    """Get or create the session dict for this MCP connection."""
    sid = ctx.session_id
    if sid not in SESSIONS:
        SESSIONS[sid] = {
            "active_model": None, # file path of the active model
            "current_graph": None, # nx.MultiDiGraph (possibly filtered)
            "original_graph": None, # pristine copy for reset
            "plugin_name": None, # which plugin owns this model
            "patterns": [],
            "html": None,
        }
    return SESSIONS[sid]


def get_active_plugin(session: dict) -> Any | None:
    """Resolve the plugin object from the session's plugin_name."""
    name = session.get("plugin_name")
    if name is None:
        return None
    return plugin_manager.get(name)
