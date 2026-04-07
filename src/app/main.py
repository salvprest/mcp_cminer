from app.utils.logger import setup_logging
logger = setup_logging("DEBUG", __name__.replace(".main", ""))

import json
import os
import tempfile
import uvicorn
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, UploadFile, File
from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
from fastmcp.utilities.lifespan import combine_lifespans

from CMiner import CMinerAPI
from cml2graph.core.formats.node_link_fmt import NodeLinkFormat

from app.core.state import (
    UPLOAD_DIR,
    PARSED_MODELS,
    SESSIONS,
    plugin_manager as _plugin_manager,
)
from app.tools.cml2graph import mcp as cml2graph_mcp
from app.tools.cminer import mcp as cminer_mcp


@lifespan
async def cminer_lifespan(server):
    cminer = CMinerAPI(
        db_file="datasets/Datasets/test/test.data",
        support=1,
        max_nodes=10,
        string_output=True,
    )
    try:
        yield {"cminer": cminer}
    finally:
        cminer.close()


mcp = FastMCP(
    name="MCP Server for CMiner",
    lifespan=cminer_lifespan,
    instructions="""
    A server for graph mining on conceptual models.

    Workflow:
    1. Upload a model file via the REST /upload endpoint.
    2. Use cml2graph_load_model to load it into your session.
    3. Use cml2graph_list_filters / cml2graph_explain_model to understand it.
    4. Use cml2graph_filter to focus on specific parts.
    5. Use cminer_run to mine patterns.
    6. Use cminer_generate_visualization to view results.

    Always call cminer_get_pattern_info or cminer_list_patterns when asked about
    specific patterns — do NOT rely on the visualization HTML for data.
    """,
)

# Mount sub-servers
mcp.mount(cml2graph_mcp)
mcp.mount(cminer_mcp)


# ====================== Resources ======================

@mcp.resource("cminer://sessions/{session_id}/private/visualization")
def get_pattern_visualization(session_id: str) -> str:
    """
    Self-contained HTML visualization for the patterns mined in the given session.
    This resource returns potentially large HTML — the pipe renders it as an
    iframe widget and never puts the full content in the LLM context.
    """
    if session_id in SESSIONS and SESSIONS[session_id].get("html"):
        return SESSIONS[session_id]["html"]
    return (
        "<h1>Visualization not found.</h1>"
        "<p>Run <code>cminer_generate_visualization</code> first.</p>"
    )


@mcp.resource("cminer://sessions/{session_id}/patterns", mime_type="application/json")
def get_pattern_data(session_id: str) -> str:
    """
    All mined patterns for the given session as JSON.
    Prefer cminer_get_pattern_info / cminer_list_patterns for targeted queries.
    """
    if session_id in SESSIONS:
        return json.dumps(
            {"patterns": SESSIONS[session_id]["patterns"]},
            ensure_ascii=False,
        )
    return json.dumps({"error": "Session not found."})


@mcp.resource("cminer://sessions/{session_id}/uploads")
def list_uploaded_files(session_id: str) -> str:
    """List files in the upload directory available for CMiner."""
    if not UPLOAD_DIR.exists():
        return json.dumps({"error": "Upload directory does not exist."})
    files = [f.name for f in UPLOAD_DIR.iterdir() if f.is_file()]
    return json.dumps(
        {"upload_dir": str(UPLOAD_DIR), "files": sorted(files)},
        ensure_ascii=False,
    )


# ====================== FastAPI app ======================

@asynccontextmanager
async def app_lifespan(app: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Upload directory ready: {UPLOAD_DIR}")
    yield
    logger.info("CMiner FastAPI app shutting down.")

# The FastAPI app mounts the MCP server at /mcp and also provides a REST endpoint for file uploads.
mcp_app = mcp.http_app(path="/")
app = FastAPI(title="CMiner API", lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan))
app.mount("/mcp", mcp_app)


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    Receive a file, detect its format via plugin manager, convert if needed,
    and persist it to UPLOAD_DIR. Returns the absolute saved path.
    """
    print(f"Received upload: {file.filename} ({file.content_type})")
    logger.debug(f"Upload content type: {file.content_type}")
    content = await file.read()
    try:
        text_content = content.decode("utf-8")
    except UnicodeDecodeError:
        return {"error": "Uploaded file is not valid UTF-8.", "saved_path": ""}

    try:
        data_content = json.loads(text_content)
    except json.JSONDecodeError:
        data_content = None

    specific_dir_name = "gSpan"
    graph = None
    plugin_name = None

    if data_content is not None:
        for p_name in _plugin_manager.available:
            plugin = _plugin_manager.get(p_name)
            if plugin.is_valid(data_content):
                def _write_model(data: dict) -> str:
                    f = tempfile.NamedTemporaryFile(
                        mode="w", suffix=".json", delete=False, encoding="utf-8"
                    )
                    json.dump(data, f, ensure_ascii=False)
                    f.close()
                    return f.name

                graph = plugin.parse(_write_model(data_content))
                text_content = NodeLinkFormat().render(graph)
                specific_dir_name = plugin.name
                plugin_name = plugin.name
                logger.info(f"File {file.filename} converted via plugin '{plugin.name}'")
                break

    spec_dir = UPLOAD_DIR / specific_dir_name
    spec_dir.mkdir(parents=True, exist_ok=True)
    dest = spec_dir / file.filename
    with dest.open("w") as fh:
        fh.write(text_content)

    saved_path = str(dest)

    # Cache the parsed graph so cml2graph_load_model doesn't re-parse
    if graph is not None and plugin_name is not None:
        PARSED_MODELS[saved_path] = {
            "graph": graph,
            "plugin_name": plugin_name,
        }

    return {
        "saved_path": saved_path,
        "plugin": plugin_name,
        "preview":    text_content[:200],
    }


@app.get("/uploads")
async def list_uploads():
    """List all uploaded files (recursive)."""
    files = []
    for f in UPLOAD_DIR.rglob("*"):
        if f.is_file():
            files.append(str(f.relative_to(UPLOAD_DIR)))
    return {"upload_dir": str(UPLOAD_DIR), "files": sorted(files)}


def main():
    logger.info("Starting CMiner MCP server…")
    uvicorn.run(app, host="0.0.0.0", port=8222)


if __name__ == "__main__":
    main()
