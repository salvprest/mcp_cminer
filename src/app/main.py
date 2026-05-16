from app.utils.logger import setup_logging
logger = setup_logging("DEBUG", __name__.replace(".main", ""))

import json
import os
import tempfile
import httpx
import uvicorn
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response as FastAPIResponse, HTMLResponse
from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
from fastmcp.utilities.lifespan import combine_lifespans

from CMiner import CMinerAPI
from cml2graph.core.formats.node_link_fmt import NodeLinkFormat

from app.core.state import (
    UPLOAD_DIR,
    PARSED_MODELS,
    SESSIONS,
    DATASETS,
    plugin_manager as _plugin_manager,
)
from app.core.auth import create_auth_verifier
from app.tools.cml2graph import mcp as cml2graph_mcp
from app.tools.cminer import mcp as cminer_mcp
from app.tools.session_management import mcp as session_mcp
from app.utils.dataset_loader import handle_dataset_upload


@lifespan
async def cminer_lifespan(server):
    cminer = CMinerAPI(
        db_file="datasets/Datasets/test/test.data",
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


mcp = FastMCP(
    name="MCP Server for CMiner",
    lifespan=cminer_lifespan,
    auth=create_auth_verifier(),
    instructions="""
    A server for graph mining on conceptual models.

    Workflow:
    1. Upload a model file via the REST /upload endpoint.
    2. Use cml2graph_load_model to load it into your session.
    3. Use cml2graph_list_filters / cml2graph_explain_model to understand it.
    4. Use cml2graph_filter to focus on specific parts.
    5. Use cminer_run to mine patterns.
    6. Use cminer_generate_visualization to view results.
    7. Use session_save to persist your work for later.
    8. Use session_list and session_load to restore previous sessions.

    Always call cminer_get_pattern_info or cminer_list_patterns when asked about
    specific patterns — do NOT rely on the visualization HTML for data.
    """,
)

# Mount sub-servers
mcp.mount(cml2graph_mcp)
mcp.mount(cminer_mcp)
mcp.mount(session_mcp)


# ====================== Resources ======================

@mcp.resource("cminer://sessions/{session_id}/private/visualization")
def get_pattern_visualization(session_id: str) -> str:
    """
    Self-contained HTML visualization for the patterns mined in the given session.
    This resource returns a lightweight HTML shell that fetches data lazily
    from the CMiner API endpoints. Open WebUI renders it as an inline artifact.
    """
    if session_id in SESSIONS and SESSIONS[session_id].get("viz_data"):
        from app.core.visualization import generate_html_visualization
        version = SESSIONS[session_id]["viz_data"].get("version", 0)
        return generate_html_visualization(session_id, version=version)
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

# Allow cross-origin requests from iframe srcdoc (opaque "null" origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

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

    # Check if it's a ZIP file (dataset)
    is_zip = (
        file.filename.lower().endswith('.zip') 
        or file.content_type in ('application/zip', 'application/x-zip-compressed')
    )
    if is_zip:# TODO: Improve this
        return await handle_dataset_upload(UPLOAD_DIR, DATASETS, file.filename, content)

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


# ====================== Visualization page & API ======================

_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=15.0)
    return _http_client


@app.get("/visualization/{session_id}", response_class=HTMLResponse)
async def visualization_page(session_id: str):
    """Serve the lightweight HTML shell for the visualization viewer."""
    if session_id not in SESSIONS or not SESSIONS[session_id].get("viz_data"):
        return HTMLResponse(
            "<h1>Session not found or visualization not generated.</h1>",
            status_code=404,
        )
    from app.core.visualization import generate_html_visualization
    return HTMLResponse(generate_html_visualization(session_id))


@app.get("/api/sessions/{session_id}/meta")
async def session_meta(session_id: str):
    """Return summary metadata for the visualization."""
    if session_id not in SESSIONS or not SESSIONS[session_id].get("viz_data"):
        return FastAPIResponse(status_code=404, content='{"error":"not found"}')
    viz = SESSIONS[session_id]["viz_data"]
    return {
        "num_patterns": len(viz.get("patterns", [])),
        "num_models": len(viz.get("models", [])),
    }


@app.get("/api/sessions/{session_id}/patterns")
async def session_patterns(
    session_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Return a page of pattern metadata + encoded PlantUML strings."""
    if session_id not in SESSIONS or not SESSIONS[session_id].get("viz_data"):
        return FastAPIResponse(status_code=404, content='{"error":"not found"}')
    all_patterns = SESSIONS[session_id]["viz_data"].get("patterns", [])
    page = all_patterns[offset : offset + limit]
    return {"patterns": page, "total": len(all_patterns)}


@app.get("/api/sessions/{session_id}/models")
async def session_models(session_id: str):
    """Return all model names + encoded PlantUML strings."""
    if session_id not in SESSIONS or not SESSIONS[session_id].get("viz_data"):
        return FastAPIResponse(status_code=404, content='{"error":"not found"}')
    return {"models": SESSIONS[session_id]["viz_data"].get("models", [])}


@app.get("/plantuml/svg/{encoded:path}")
async def plantuml_svg_proxy(encoded: str):
    """
    Async proxy for PlantUML SVG requests.  The browser calls this endpoint;
    the server forwards to the internal PlantUML container (or public server).
    """
    from app.utils.plantuml_helper import PLANTUML_SERVER
    url = f"{PLANTUML_SERVER}/svg/{encoded}"
    client = _get_http_client()
    try:
        resp = await client.get(url)
        return FastAPIResponse(
            content=resp.content,
            media_type="image/svg+xml",
            status_code=resp.status_code,
        )
    except httpx.HTTPError as exc:
        error_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="60">'
            '<rect width="100%" height="100%" fill="#fff0f0" rx="4"/>'
            f'<text x="10" y="35" font-family="monospace" font-size="13" fill="#c0392b">PlantUML error: {exc}</text>'
            '</svg>'
        )
        return FastAPIResponse(
            content=error_svg, media_type="image/svg+xml", status_code=502,
        )


def main():
    logger.info("Starting CMiner MCP server…")
    uvicorn.run(app, host="0.0.0.0", port=8222)


if __name__ == "__main__":
    main()
