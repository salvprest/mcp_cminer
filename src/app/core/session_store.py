import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
import networkx as nx
from networkx.readwrite import json_graph

class SessionStore:
    """Persistent storage for MCP sessions, scoped by client_id."""

    def __init__(self, base_dir: Path = Path("data/saved_sessions")):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_client_dir(self, client_id: str) -> Path:
        """Get the directory for a specific client."""
        client_dir = self.base_dir / client_id
        client_dir.mkdir(parents=True, exist_ok=True)
        return client_dir

    def save_session(
        self,
        client_id: str,
        session_id: str,
        session_data: Dict,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> str:
        """Save a session with all its data (excluding HTML)."""
        client_dir = self._get_client_dir(client_id)
        session_dir = client_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        # Metadata
        metadata = {
            "session_id": session_id,
            "client_id": client_id,
            "name": name or f"Session {session_id[:8]}",
            "description": description or "",
            "created_at": session_data.get("created_at", datetime.now().isoformat()),
            "saved_at": datetime.now().isoformat(),
            "num_models": len(session_data.get("models", {})),
            "num_patterns": len(session_data.get("patterns", [])),
            "active_model": session_data.get("active_model"),
        }

        with open(session_dir / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

        # Models (with graph serialization)
        models_data = {}
        for key, entry in session_data.get("models", {}).items():
            models_data[key] = {
                "file_path": entry["file_path"],
                "plugin_name": entry["plugin_name"],
                # TODO: this can store very large graphs and a very hight ammount of them,
                # we need to store the filters applied to the the original graph, not the whole graph
                "original_graph": json_graph.node_link_data(entry["original_graph"]),
                "current_graph": json_graph.node_link_data(entry["current_graph"]),
            }

        with open(session_dir / "models.json", "w") as f:
            json.dump(models_data, f, indent=2)
        with open(session_dir / "patterns.json", "w") as f:
            json.dump(session_data.get("patterns", []), f, indent=2)

        return str(session_dir)

    def load_session(self, client_id: str, session_id: str) -> Optional[Dict]:
        """Load a saved session for a specific client."""
        client_dir = self._get_client_dir(client_id)
        session_dir = client_dir / session_id

        if not session_dir.exists():
            return None

        with open(session_dir / "metadata.json", "r") as f:
            metadata = json.load(f)

        models = {}
        models_file = session_dir / "models.json"
        if models_file.exists():
            with open(models_file, "r") as f:
                models_data = json.load(f)

            for key, entry in models_data.items():
                models[key] = {
                    "file_path": entry["file_path"],
                    "plugin_name": entry["plugin_name"],
                    "original_graph": json_graph.node_link_graph(entry["original_graph"]),
                    "current_graph": json_graph.node_link_graph(entry["current_graph"]),
                }

        # Load patterns
        patterns = []
        patterns_file = session_dir / "patterns.json"
        if patterns_file.exists():
            with open(patterns_file, "r") as f:
                patterns = json.load(f)

        return {
            "models": models,
            "patterns": patterns,
            "viz_data": None,  # Will be regenerated on demand
            "active_model": metadata.get("active_model"),
            "created_at": metadata.get("created_at"),
        }

    def list_sessions(self, client_id: str) -> List[Dict]:
        """List all saved sessions for a specific client."""
        client_dir = self._get_client_dir(client_id)
        sessions = []

        for session_dir in client_dir.iterdir():
            if session_dir.is_dir():
                metadata_file = session_dir / "metadata.json"
                if metadata_file.exists():
                    with open(metadata_file, "r") as f:
                        sessions.append(json.load(f))

        return sorted(sessions, key=lambda x: x["saved_at"], reverse=True)

    def delete_session(self, client_id: str, session_id: str) -> bool:
        """Delete a saved session for a specific client."""
        client_dir = self._get_client_dir(client_id)
        session_dir = client_dir / session_id

        if session_dir.exists():
            shutil.rmtree(session_dir)
            return True
        return False

    def get_session_info(self, client_id: str, session_id: str) -> Optional[Dict]:
        """Get metadata about a saved session."""
        client_dir = self._get_client_dir(client_id)
        session_dir = client_dir / session_id
        metadata_file = session_dir / "metadata.json"

        if metadata_file.exists():
            with open(metadata_file, "r") as f:
                return json.load(f)
        return None