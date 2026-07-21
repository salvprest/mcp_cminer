"""
title: MCP Client Pipe
description: MCP client for Open WebUI — context-safe visualization + pattern query support.
requirements: mcp, fastmcp, pydantic, aiohttp, json_repair
"""

import logging
import json
import html
import re
import os
import hashlib
import base64
import time
import aiohttp
import textwrap

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field
from contextlib import AsyncExitStack

from open_webui.utils.chat import generate_chat_completion
from open_webui.models.users import Users
from fastmcp import Client
from fastmcp.client.logging import LogMessage
from json_repair import repair_json

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

PIPE_NAME = "MCP_Client_Pipe"


def _setup_logger() -> logging.Logger:
    lg = logging.getLogger(PIPE_NAME)
    if not lg.handlers:
        lg.setLevel(logging.DEBUG)
        h = logging.StreamHandler()
        h.set_name(PIPE_NAME)
        h.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        lg.addHandler(h)
        lg.propagate = False
    return lg


logger = _setup_logger()

StatusEmitter = Callable[[str, str, bool], Awaitable[None]]

# ---------------------------------------------------------------------------
# Global session cache
# ---------------------------------------------------------------------------

SESSION_TTL_SECONDS = 3600


@dataclass
class CachedSession:
    manager: "MCPClientManager"
    last_used: float = field(default_factory=time.time)


GLOBAL_MCP_SESSIONS: Dict[str, CachedSession] = {}


async def _cleanup_stale_sessions() -> None:
    now = time.time()
    stale = [
        cid
        for cid, cs in GLOBAL_MCP_SESSIONS.items()
        if now - cs.last_used > SESSION_TTL_SECONDS
    ]
    for cid in stale:
        logger.info(f"Evicting stale MCP session: {cid}")
        try:
            await GLOBAL_MCP_SESSIONS[cid].manager.cleanup()
        except Exception as exc:
            logger.warning(f"Error cleaning up session {cid}: {exc}")
        del GLOBAL_MCP_SESSIONS[cid]


# ---------------------------------------------------------------------------
# URI patterns
# ---------------------------------------------------------------------------

_RESOURCE_URI_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+\-.]+://\S+")
_SKIP_SCHEMES = ("http://", "https://", "ftp://", "mailto:", "tel:")

# URIs matching this pattern are visualization blobs — never put them in the
# LLM context; they are fetched separately and rendered only in ui_blocks.
_VISUALIZATION_URI_RE = re.compile(r"cminer://[^/]+/[^/]+/private/visualization")

_INTERNAL_URI_LINE_RE = re.compile(
    r"[^\n]*"
    r"(?:`?)"
    r"[a-zA-Z][a-zA-Z0-9+\-.]+://"
    r"(?!https?://|ftp://|mailto:|tel:)"
    r"\S+"
    r"(?:`?)"
    r"[^\n]*",
)


def _strip_internal_uris(text: str) -> str:
    cleaned = _INTERNAL_URI_LINE_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------


def _truncate_message(msg: Dict, max_chars: int) -> Dict:
    """Return msg with content truncated to max_chars (strings only)."""
    content = msg.get("content")
    if not isinstance(content, str):
        return msg
    if len(content) <= max_chars:
        return msg
    truncated = content[:max_chars] + "\n…[truncated — content too large for context]"
    return {**msg, "content": truncated}


def _build_history(
    messages: List[Dict],
    max_messages: int,
    max_chars_per_msg: int,
) -> List[Dict]:
    """
    Trim and sanitise conversation history from Open WebUI.

    - Skips system messages (we build our own).
    - Keeps only the last `max_messages` non-system turns.
    - Truncates any message whose content exceeds `max_chars_per_msg`.

    This prevents context-window overflow caused by large HTML visualizations
    being replayed verbatim in subsequent turns.
    """
    non_system = [m for m in messages if m.get("role") != "system"]
    # Strip out Open WebUI's RAG context
    non_system_no_rag = []
    for msg in non_system:
        # Strip out Open WebUI's auto-injected RAG context
        clean_content = re.sub(
            r'<context>.*?</context>', 
            '', 
            msg.get("content", ""), 
            flags=re.DOTALL
        ).strip()
        non_system_no_rag.append({"role": msg["role"], "content": clean_content})

    recent = non_system_no_rag[-max_messages:] if max_messages > 0 else non_system_no_rag
    return [_truncate_message(m, max_chars_per_msg) for m in recent]


# ---------------------------------------------------------------------------
# Session container
# ---------------------------------------------------------------------------


@dataclass
class SessionContainer:
    client: Client
    tasks: List[Dict] = field(default_factory=list)
    resources: List[Dict] = field(default_factory=list)
    resource_templates: List[Dict] = field(default_factory=list)
    prompts: List[Dict] = field(default_factory=list)
    uploaded_files: Dict[str, Tuple[str, str]] = None


# ---------------------------------------------------------------------------
# MCP Client Manager
# ---------------------------------------------------------------------------


class MCPClientManager:
    def __init__(self, mcp_config: str, emit_status: StatusEmitter):
        self.mcp_config = mcp_config
        self.sessions: Dict[str, SessionContainer] = {}
        self.available_tools: List[Dict] = []
        self.exit_stack = AsyncExitStack()
        self.emit_status = emit_status

    async def connect_to_servers(self) -> None:
        try:
            servers = json.loads(self.mcp_config).get("mcpServers", {})
        except json.JSONDecodeError as exc:
            logger.exception("Invalid MCP config JSON")
            await self.emit_status("error", f"Invalid MCP config: {exc}", True)
            return
        logger.info(f"Found {len(servers)} MCP server(s)")
        for name, cfg in servers.items():
            await self._connect_one(name, cfg)

    async def _connect_one(self, server_name: str, cfg: Dict) -> None:
        url = cfg.get("url")
        token = cfg.get("token")
        if not url:
            logger.warning(f"Server '{server_name}' has no URL – skipped")
            return
        try:

            async def _log(msg: LogMessage, _sn: str = server_name) -> None:
                data = msg.data if isinstance(msg.data, dict) else {}
                await self.emit_status(
                    "info", f"{_sn}: {data.get('msg', str(msg))}", False
                )

            sc = SessionContainer(client=Client(url, log_handler=_log, auth=token))
            self.sessions[server_name] = sc
            await self.exit_stack.enter_async_context(sc.client)

            tools = await sc.client.list_tools()
            for t in tools:
                self.available_tools.append(
                    {
                        "id": t.name,
                        "description": t.description,
                        "input_schema": t.inputSchema,
                        "server": server_name,
                        "requires_task": self._needs_task(t),
                    }
                )

            try:
                sc.resources = [
                    {
                        "uri": str(r.uri),
                        "name": getattr(r, "name", str(r.uri)),
                        "description": getattr(r, "description", ""),
                        "mime_type": getattr(r, "mimeType", None),
                        "server": server_name,
                    }
                    for r in await sc.client.list_resources()
                ]
            except Exception as exc:
                logger.warning(f"Could not list resources from {server_name}: {exc}")

            try:
                sc.resource_templates = [
                    {
                        "uri_template": str(getattr(t, "uriTemplate", t)),
                        "name": getattr(t, "name", str(t)),
                        "description": getattr(t, "description", ""),
                        "mime_type": getattr(t, "mimeType", None),
                        "server": server_name,
                    }
                    for t in await sc.client.list_resource_templates()
                ]
            except Exception as exc:
                logger.warning(
                    f"Could not list resource templates from {server_name}: {exc}"
                )

            try:
                sc.prompts = [
                    {
                        "name": p.name,
                        "description": getattr(p, "description", ""),
                        "arguments": [
                            {
                                "name": a.name,
                                "description": getattr(a, "description", ""),
                                "required": getattr(a, "required", False),
                            }
                            for a in (getattr(p, "arguments", []) or [])
                        ],
                        "server": server_name,
                    }
                    for p in await sc.client.list_prompts()
                ]
            except Exception as exc:
                logger.warning(f"Could not list prompts from {server_name}: {exc}")

            logger.info(
                f"Connected to {server_name}: {len(tools)} tools, "
                f"{len(sc.resources)} resources, {len(sc.resource_templates)} templates, "
                f"{len(sc.prompts)} prompts"
            )
            await self.emit_status("info", f"Connected to {server_name}", False)

        except Exception as exc:
            logger.error(f"Failed to connect to {server_name}: {exc}")
            await self.emit_status(
                "error", f"Failed to connect to {server_name}: {exc}", False
            )

    @staticmethod
    def _needs_task(tool) -> bool:
        exe = getattr(tool, "execution", None)
        if exe is None:
            return False
        if hasattr(exe, "taskSupport"):
            return exe.taskSupport == "required"
        if isinstance(exe, dict):
            return exe.get("taskSupport") == "required"
        return False

    async def call_tool(self, tool_name: str, tool_args: Dict) -> Tuple[int, str]:
        cfg = next((t for t in self.available_tools if t["id"] == tool_name), None)
        if not cfg or cfg["server"] not in self.sessions:
            return -1, f"Tool '{tool_name}' not available or server disconnected."
        session = self.sessions[cfg["server"]]
        try:
            if cfg.get("requires_task"):
                task = await session.client.call_tool(tool_name, tool_args, task=True)

                async def _on_status(status) -> None:
                    tid = getattr(status, "taskId", "?")
                    lvl = "error" if status.status == "failed" else "info"
                    await self.emit_status(
                        lvl,
                        f"Task [{tid}] {status.status}: {status.statusMessage}",
                        False,
                    )

                task.on_status_change(_on_status)
                session.tasks.append({"tool_name": tool_name, "task": task})
                tool_result = await task.result()
            else:
                tool_result = await session.client.call_tool(tool_name, tool_args)
            return 0, self._fmt_result(tool_result)
        except Exception as exc:
            logger.exception(f"Error executing {tool_name}")
            return -1, f"Error executing '{tool_name}': {exc}"

    @staticmethod
    def _fmt_result(result) -> str:
        if hasattr(result, "content") and result.content:
            items = (
                result.content if isinstance(result.content, list) else [result.content]
            )
            return "\n".join(
                item.text if hasattr(item, "text") else str(item) for item in items
            )
        return str(result)

    async def read_resource(self, uri: str) -> Tuple[int, str, Optional[str]]:
        target = next(
            (
                sn
                for sn, s in self.sessions.items()
                if any(r["uri"] == uri for r in s.resources)
            ),
            None,
        )
        candidates = (
            [(target, self.sessions[target])] if target else list(self.sessions.items())
        )
        for sn, session in candidates:
            try:
                result = await session.client.read_resource(uri)
                contents = (
                    result
                    if isinstance(result, list)
                    else getattr(result, "contents", None)
                )
                if contents:
                    obj = contents[0]
                    mime = getattr(obj, "mimeType", None)
                    if hasattr(obj, "text") and obj.text is not None:
                        return 0, obj.text, mime
                    if hasattr(obj, "blob") and obj.blob is not None:
                        return (
                            0,
                            base64.b64encode(obj.blob).decode("ascii"),
                            mime or "application/octet-stream",
                        )
                return 0, str(result), None
            except Exception as exc:
                logger.debug(f"Could not read '{uri}' from {sn}: {exc}")
        return -1, f"Resource '{uri}' could not be fetched.", None

    async def get_prompt(
        self, name: str, arguments: Optional[Dict] = None
    ) -> Tuple[int, List[Dict]]:
        target = next(
            (
                sn
                for sn, s in self.sessions.items()
                if any(p["name"] == name for p in s.prompts)
            ),
            None,
        )
        if not target:
            return -1, []
        try:
            result = await self.sessions[target].client.get_prompt(
                name, arguments or {}
            )
            return 0, [
                {
                    "role": m.role,
                    "content": (
                        m.content.text if hasattr(m.content, "text") else str(m.content)
                    ),
                }
                for m in (getattr(result, "messages", []) or [])
            ]
        except Exception as exc:
            logger.error(f"Error fetching prompt '{name}': {exc}")
            return -1, []

    def list_all_resources(self) -> List[Dict]:
        return [r for s in self.sessions.values() for r in s.resources]

    def list_all_resource_templates(self) -> List[Dict]:
        return [t for s in self.sessions.values() for t in s.resource_templates]

    def list_all_prompts(self) -> List[Dict]:
        return [p for s in self.sessions.values() for p in s.prompts]

    def list_all_uploaded_files(self) -> Dict[str, Tuple[str, str]]:
        """File hash -> (file_name, remote_path) mapping cache."""
        files = {}
        for s in self.sessions.values():
            if s.uploaded_files:
                files.update(s.uploaded_files)
        return files

    def register_uploaded_file(
        self, file_hash: str, file_name: str, remote_path: str
    ) -> None:
        """Cache file hash -> (name, remote_path) for the active session."""
        for s in self.sessions.values():
            if s.uploaded_files is None:
                s.uploaded_files = {}
            s.uploaded_files[file_hash] = (file_name, remote_path)
            return

    async def cleanup(self) -> None:
        await self.exit_stack.aclose()


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

_BINARY_MIMES = frozenset(
    (
        "application/octet-stream",
        "application/pdf",
        "application/zip",
        "application/gzip",
    )
)


def _render_resource(uri: str, content: str, mime_type: Optional[str]) -> str:
    is_binary = mime_type and (
        mime_type.startswith(("image/", "audio/", "video/"))
        or mime_type in _BINARY_MIMES
    )
    if is_binary:
        if mime_type and mime_type.startswith("image/"):
            return (
                f"<details open><summary>📷 Resource</summary>"
                f'<img src="data:{mime_type};base64,{content}" '
                f'alt="resource" style="max-width:100%"/></details>\n'
            )
        size_kb = len(content) * 3 // 4 // 1024
        return (
            f"<details><summary>📦 Binary (~{size_kb} KB, {mime_type})</summary>"
            f"<p>Binary content — not displayed inline.</p></details>\n"
        )

    stripped = content.lstrip()[:20].lower()
    if mime_type == "text/html" or stripped.startswith(("<!doctype", "<html")):
        return f"\n**📊 Interactive Visualization**\n\n```html\n{content}\n```\n"

    if mime_type in ("application/json", "text/json") or content.lstrip()[:1] in (
        "{",
        "[",
    ):
        try:
            pretty = json.dumps(json.loads(content), indent=2, ensure_ascii=False)
            return f"\n**📄 Data**\n\n```json\n{pretty}\n```\n"
        except (json.JSONDecodeError, ValueError):
            pass

    return (
        f"<details open><summary>📄 Resource</summary>"
        f"<pre>{html.escape(content)}</pre></details>\n"
    )


def _tool_detail_html(name: str, args: Dict, result: str) -> str:
    escaped_result = html.escape(result).replace("\n", "&#10;")
    return (
        f'<details type="tool_calls" done="true" '
        f'name="{html.escape(name)}" '
        f'arguments="{html.escape(json.dumps(args))}" '
        f'result="{escaped_result}">\n'
        f"<summary>🔧 Executed: {html.escape(name)}</summary>\n</details>\n"
    )


# ---------------------------------------------------------------------------
# Native tool-calling helpers
# ---------------------------------------------------------------------------


def _mcp_tools_to_openai_tools(mcp_tools: List[Dict]) -> List[Dict]:
    openai_tools = []
    for t in mcp_tools:
        schema = dict(t["input_schema"]) if t["input_schema"] else {}
        if "type" not in schema:
            schema["type"] = "object"
        schema.pop("additionalProperties", None)
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": t["id"],
                    "description": t["description"] or "",
                    "parameters": schema,
                },
            }
        )
    return openai_tools


def _parse_tool_calls_from_response(response: Dict) -> List[Dict]:
    message = response["choices"][0]["message"]
    tool_calls = message.get("tool_calls")
    if not tool_calls:
        return []
    parsed = []
    for tc in tool_calls:
        func = tc.get("function", {})
        name = func.get("name", "")
        raw_args = func.get("arguments", "{}")
        try:
            args = json.loads(repair_json(raw_args))
        except (json.JSONDecodeError, ValueError):
            try:
                args = json.loads(raw_args)
            except (json.JSONDecodeError, ValueError):
                args = {}
                logger.warning(
                    f"Could not parse arguments for tool '{name}': {raw_args[:200]}"
                )
        parsed.append(
            {
                "id": tc.get("id", f"call_{name}_{len(parsed)}"),
                "name": name,
                "arguments": args if isinstance(args, dict) else {},
            }
        )
    return parsed


# Matches the exact HTML that _tool_detail_html() produces, so detection
# is anchored to OUR own format rather than any arbitrary LLM output.
_FAKE_TOOL_RE = re.compile(
    r'<details[^>]+type="tool_calls"[^>]+name="([^"]+)"'
    r'|<summary>🔧 Executed:\s*([\w-]+)</summary>',
    re.IGNORECASE,
)

# Matches visualization HTML the LLM generates itself instead of calling
# the visualization tool. Anchored to the title our server always emits.
_FAKE_VISUALIZATION_RE = re.compile(
    r'<title>\s*CMiner\s+Visualization\s*</title>',
    re.IGNORECASE,
)

_MAX_FAKE_RETRIES = 3


def _detect_fake_tool_execution(
    content: str, actual_tool_calls: List[Dict]
) -> Tuple[List[str], str]:
    """
    Detect whether the LLM faked tool execution in *content*.

    Returns (fake_tool_names, kind) where kind is:
      - "fake_call"  — the model mimicked our tool-call HTML widget
      - "raw_html"   — the model returned raw visualization HTML directly
      - ""           — no fake detected (fake_tool_names will be [])
    """
    if actual_tool_calls or not content:
        return [], ""
    fake = [m.group(1) or m.group(2) for m in _FAKE_TOOL_RE.finditer(content)]
    if fake:
        return fake, "fake_call"
    if _FAKE_VISUALIZATION_RE.search(content):
        return ["cminer_generate_visualization"], "raw_html"
    return [], ""


def _unwrap_llm_response(resp) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Safely unwrap generate_chat_completion result.
    Returns (response_dict, error_string) — one is always None.
    """
    if isinstance(resp, dict):
        if "choices" in resp:
            return resp, None
        return None, f"Unexpected response shape: {json.dumps(resp)[:300]}"
    body_bytes = getattr(resp, "body", None)
    if body_bytes is not None:
        try:
            body = json.loads(body_bytes)
            err = body.get("error", body)
            msg = (
                err.get("message") or err.get("detail") or json.dumps(err)
                if isinstance(err, dict)
                else str(err)
            )
            return None, msg
        except Exception:
            return None, body_bytes.decode("utf-8", errors="replace")[:500]
    return None, f"Non-dict response from LLM: {type(resp).__name__}"

# ---------------------------------------------------------------------------
# Files helpers
# ---------------------------------------------------------------------------

def _get_file_hash(filepath: str) -> str:
    """Calculate the SHA-256 hash of a file."""
    hasher = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            # Read and update hash string value in blocks of 4K
            for byte_block in iter(lambda: f.read(4096), b""):
                hasher.update(byte_block)
        return hasher.hexdigest()
    except Exception as e:
        logger.error(f"Failed to hash file {filepath}: {e}")
        return ""
# ---------------------------------------------------------------------------
# Prompt command detection
# ---------------------------------------------------------------------------

_PROMPT_CMD_RE = re.compile(r"^/(\S+)\s*(.*)?$", re.DOTALL)


def _parse_prompt_command(text: str) -> Optional[Tuple[str, Dict[str, str]]]:
    text = text.strip()
    m = _PROMPT_CMD_RE.match(text)
    if not m:
        return None
    name = m.group(1)
    rest = (m.group(2) or "").strip()
    arguments: Dict[str, str] = {}
    if rest:
        for pair in re.findall(r'(\w+)=(?:"([^"]*)"|(\S+))', rest):
            arguments[pair[0]] = pair[1] if pair[1] else pair[2]
        if not arguments:
            arguments["input"] = rest
    return name, arguments


# ---------------------------------------------------------------------------
# Resource selection helpers
# ---------------------------------------------------------------------------


def _match_resources_for_query(
    query: str, resources: List[Dict], templates: List[Dict]
) -> List[str]:
    query_lower = query.lower()
    words = [w for w in query_lower.split() if len(w) > 3]
    if not words:
        return []
    matched = []
    for r in resources:
        name = (r.get("name") or "").lower()
        desc = (r.get("description") or "").lower()
        uri = (r.get("uri") or "").lower()
        if any(w in name or w in desc or w in uri for w in words):
            matched.append(r["uri"])
    return matched


# ---------------------------------------------------------------------------
# Pipe
# ---------------------------------------------------------------------------


class Pipe:
    class Valves(BaseModel):
        MODEL: str = Field(default="chatgpt-4o", description="Model to use")
        DISPLAY_NAME: str = Field(default="Conceptual Model Assistant", description="Display name")
        TEMPERATURE: float = Field(default=0.3)
        MAX_TOKENS: int = Field(default=12192)
        MAX_ITERATIONS: int = Field(default=30)

        # ── Context window management ────────────────────────────────────
        MAX_HISTORY_MESSAGES: int = Field(
            default=10,
            description=(
                "Maximum conversation turns sent to the LLM."
                "Older turns are dropped. Increase only if the model has a large context window."
            ),
        )
        MAX_MESSAGE_CONTENT_CHARS: int = Field(
            default=6000,
            description=(
                "Maximum characters per message in conversation history."
                "Messages exceeding this are truncated before being sent to the LLM."
                "Increase if you need more context, decrease if you hit context limit errors."
            ),
        )
        MAX_TOOL_RESULT_CHARS: int = Field(
            default=8000,
            description=(
                "Maximum characters of a tool result stored in the conversation. "
                "The full result is still rendered in the UI; only the LLM copy is capped."
            ),
        )

        # ── Resources ────────────────────────────────────────────────────
        AUTO_FETCH_RESOURCE_URIS: bool = Field(
            default=True,
            description="Auto-fetch MCP resource URIs found in tool results.",
        )
        AUTO_ATTACH_RESOURCES: bool = Field(
            default=True,
            description="Auto-attach relevant resources as context based on the user query.",
        )
        RESOURCE_CONTEXT_LIMIT: int = Field(
            default=4000,
            description="Max chars of resource content injected into LLM context.",
        )

        # ── Misc ─────────────────────────────────────────────────────────
        STRIP_INTERNAL_URIS: bool = Field(
            default=True,
            description="Remove internal MCP URIs from the final user-visible answer.",
        )
        DELETE_LOCAL_FILES_AFTER_UPLOAD: bool = Field(
            default=False,
            description="Delete local files after uploading to the MCP server.",
        )
        PROMPT_PREFIX: str = Field(
            default="/",
            description="Prefix that triggers MCP prompt invocation (e.g. '/summarize code=main.py').",
        )
        SYSTEM_PROMPT: str = Field(
            default=textwrap.dedent(
                """
                You are a Conceptual Model Assistant.
                You analyze, filter, and discover patterns in conceptual models (such as OntoUML, ArchiMate, etc.). 
                You can:
                - Load & Analyze Models: load an uploaded model file, summarize it's content, and explain its domain semantics based on its metamodel.
                - Filter & Focus: filter loaded models to focus on specific parts, list available filters, reset to the original graph.
                - Mine Graphs Patterns: run CMiner to discover frequent subgraphs or patterns in the loaded models. Dynamically adjust parameters like `min_support`, `min_nodes`, and `max_nodes` to refine the search.
                - Query & Explore: filter mined patterns by criteria. get stats on specific pattern IDs, generate a full session summary.
                - Visualize: build an interactive HTML for the loaded models graph and/or the discovered patterns.
                - Save & Load Sessions: list saved sessions, load previous sessions for continued analysis.

                Interative visualization, to assure to the user an intercative visualization experience,
                you have to generate or update the visualization after:
                - Mining patterns,
                - Loading, removing, or clearing models,
                - Applying filters,
                - Loading a saved session.

                Rules:
                - Load all the models files passed to you, unless the user specifies the models that want to load.
                - Filter all the models files if the user do not specify the model to filter.
                - ALWAYS list all available filters before applying any filter.
                - ALWAYS execute the tools needed to answer the user's query.
                - Do NOT fake the tools execution.
                - Do NOT include internal resource URIs (cminer://) in your responses.

                To get started, upload a model file (using the attachment button).
                """
            ),
        )
        MCP_AUTH_TOKEN: str = Field(
            default="dev-token-admin", #TODO: use this for the token field in the servers config
            description="Bearer token for MCP authentication."
        )
        MCP_CONFIG: str = Field(
            default=json.dumps(
                {
                    "mcpServers": {
                        "cminer_mcp": {
                            "type": "streamable_http",
                            "url": "http://host.docker.internal:8222/mcp",
                            "token": "dev-token-admin"
                        }
                    }
                }
            ),
        )
        FASTAPI_UPLOAD_URL: str = Field(
            default="http://host.docker.internal:8222/upload",
        )

    def __init__(self):
        self.valves = self.Valves()

    def pipes(self) -> list[dict[str, str]]:
        return [{"id": "mcp-client", "name": self.valves.DISPLAY_NAME}]

    # ── Status emitter ────────────────────────────────────────────────────

    async def _emit(self, emitter, level: str, message: str, done: bool) -> None:
        if emitter:
            await emitter(
                {
                    "type": "status",
                    "data": {
                        "status": "complete" if done else "in_progress",
                        "level": level,
                        "description": message,
                        "done": done,
                    },
                }
            )

    # ── System prompt ─────────────────────────────────────────────────────

    def _build_system_prompt(
        self,
        uploaded_files: Optional[List[Dict]] = None,
        resource_context: Optional[List[Tuple[str, str, Optional[str]]]] = None,
        prompt_messages: Optional[List[Dict]] = None,
    ) -> str:
        parts: List[str] = [self.valves.SYSTEM_PROMPT]

        if prompt_messages:
            prompt_text_parts = []
            for m in prompt_messages:
                if m["role"] == "system":
                    prompt_text_parts.append(m["content"])
                else:
                    prompt_text_parts.append(
                        f"[Context from prompt — {m['role']}]: {m['content']}"
                    )
            parts.append("## Prompt Instructions\n" + "\n\n".join(prompt_text_parts))

        if resource_context:
            res_parts = []
            for uri, content, mime in resource_context:
                limit = self.valves.RESOURCE_CONTEXT_LIMIT
                truncated = (
                    content[:limit] + "\n…(truncated)"
                    if len(content) > limit
                    else content
                )
                res_parts.append(f"### Resource: {uri}\n```\n{truncated}\n```")
            parts.append(
                "## Reference Context (auto-attached resources)\n"
                "The following resources were automatically loaded as relevant context.\n\n"
                + "\n\n".join(res_parts)
            )

        if uploaded_files:
            file_lines = "\n".join(
                f"- **{f['name']}** -> remote path: `{f['remote_path']}`"
                for f in uploaded_files
            )
            parts.append(
                f"## Uploaded Files (already on the MCP server)\n{file_lines}\n\n"
                "Pass the **remote path** to any tool that accepts a file path. "
                "Do NOT ask the user for the path."
            )

        return "\n\n".join(parts)

    # ── File upload ───────────────────────────────────────────────────────

    async def _push_files_to_remote(self, files: list, emitter, chat_id: str) -> List[Dict]:
        cached = GLOBAL_MCP_SESSIONS[chat_id]
        mcp_manager = cached.manager
        results: List[Dict] = []
        async with aiohttp.ClientSession() as http:
            for item in files:
                path = item.get("path") or item.get("file", {}).get("path")
                fname = (
                    item.get("name")
                    or item.get("file", {}).get("filename")
                    or os.path.basename(path or "file")
                )
                if not path or not os.path.exists(path):
                    logger.warning(
                        f"Skipping '{fname}': local path not found ({path!r})"
                    )
                    results.append(
                        {
                            "name": fname,
                            "remote_path": None,
                            "status": "error",
                            "detail": f"Local path not found: {path!r}",
                        }
                    )
                    continue
                try:
                    # Calculate the unique content hash
                    file_hash = _get_file_hash(path)
                    # Check whether the same content was already uploaded in this chat
                    uploaded_files = mcp_manager.list_all_uploaded_files()
                    if file_hash and file_hash in uploaded_files:
                        original_file_name, cached_remote = uploaded_files[file_hash]
                        logger.debug(
                            f"Skipping '{fname}': identical content already uploaded as "
                            f"'{original_file_name}' -> {cached_remote}."
                        )
                        results.append(
                            {
                                "name": fname,
                                "remote_path": cached_remote,
                                "status": "skipped",
                                "detail": (
                                    f"File with identical content already uploaded as "
                                    f"'{original_file_name}' ({cached_remote}). Treat them as the same file."
                                ),
                            }
                        )
                        continue
                    logger.debug(f"Uploading '{fname}' to MCP server (hash: {file_hash})")
                    await self._emit(emitter, "info", f"Uploading {fname}…", False)
                    with open(path, "rb") as fh:
                        form = aiohttp.FormData()
                        form.add_field("file", fh, filename=fname)
                        async with http.post(
                            self.valves.FASTAPI_UPLOAD_URL, data=form
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                remote = data.get("saved_path", "unknown")
                                await self._emit(
                                    emitter,
                                    "info",
                                    f"Uploaded {fname} → {remote}",
                                    False,
                                )
                                mcp_manager.register_uploaded_file(file_hash, fname, remote)
                                results.append(
                                    {
                                        "name": fname,
                                        "remote_path": remote,
                                        "status": "ok",
                                        "detail": f"Saved at {remote}",
                                    }
                                )
                                if self.valves.DELETE_LOCAL_FILES_AFTER_UPLOAD:
                                    try:
                                        os.remove(path)
                                    except OSError:
                                        pass
                            else:
                                body = await resp.text()
                                results.append(
                                    {
                                        "name": fname,
                                        "remote_path": None,
                                        "status": "error",
                                        "detail": f"HTTP {resp.status}: {body[:200]}",
                                    }
                                )
                except Exception as exc:
                    results.append(
                        {
                            "name": fname,
                            "remote_path": None,
                            "status": "error",
                            "detail": str(exc),
                        }
                    )
        return results

    # ── Resource fetching (application-controlled) ────────────────────────

    async def _fetch_resources_for_context(
        self, uris: List[str], mgr: MCPClientManager, emitter
    ) -> List[Tuple[str, str, Optional[str]]]:
        results: List[Tuple[str, str, Optional[str]]] = []
        for uri in uris:
            await self._emit(emitter, "info", f"Loading resource: {uri}…", False)
            status, content, mime = await mgr.read_resource(uri)
            if status == 0:
                results.append((uri, content, mime))
            else:
                logger.warning(f"Failed to load resource {uri}: {content}")
        return results

    async def _auto_fetch_resource_uris(
        self,
        text: str,
        mgr: MCPClientManager,
        fetched: Set[str],
        emitter,
        skip_visualization: bool = False,
    ) -> List[Tuple[str, str, Optional[str]]]:
        """
        Fetch resources whose URIs appear in text (application-controlled).

        `skip_visualization=True` skips large HTML visualization URIs so they
        don't end up in the LLM context when scanning the final answer text.
        Visualization URIs are still fetched during tool execution (where
        their content goes to ui_blocks, not chat_msgs).
        """
        results: List[Tuple[str, str, Optional[str]]] = []
        for match in _RESOURCE_URI_RE.finditer(text):
            uri = match.group(0).rstrip(".,;)\"'`")
            if any(uri.startswith(s) for s in _SKIP_SCHEMES):
                continue
            if uri in fetched:
                continue
            if skip_visualization and _VISUALIZATION_URI_RE.search(uri):
                logger.debug(f"Skipping visualization URI from LLM text: {uri}")
                continue
            fetched.add(uri)
            await self._emit(emitter, "info", f"Auto-fetching resource: {uri}…", False)
            status, content, mime = await mgr.read_resource(uri)
            if status == 0:
                results.append((uri, content, mime))
            else:
                logger.warning(f"Failed to auto-fetch {uri}: {content}")
        return results

    # ── Tool execution ────────────────────────────────────────────────────

    async def _execute_tool_calls(
        self,
        tool_calls: List[Dict],
        mgr: MCPClientManager,
        ui_blocks: List[str],
        fetched_uris: Set[str],
        emitter,
    ) -> List[Dict]:
        """
        Execute tool calls and return tool-role messages for the conversation.

        Key design:
        - Tool results are truncated to MAX_TOOL_RESULT_CHARS before going into
          chat_msgs (LLM context).
        - Auto-fetched visualization resources go into ui_blocks ONLY — they
          are never stored in chat_msgs, preventing context overflow.
        - The full tool result (including visualization HTML) is always rendered
          to the UI via ui_blocks.
        """
        tool_messages = []
        max_chars = self.valves.MAX_TOOL_RESULT_CHARS

        for tc in tool_calls:
            tname = tc["name"]
            targs = tc["arguments"]
            tc_id = tc["id"]

            await self._emit(emitter, "info", f"Running tool: {tname}…", False)
            status, result = await mgr.call_tool(tname, targs)

            # Full result -> UI detail widget
            ui_blocks.append(_tool_detail_html(tname, targs, result))

            # Truncated result -> LLM context
            if len(result) > max_chars:
                context_result = (
                    result[:max_chars] + "\n…[truncated — see UI for full output]"
                )
                logger.info(
                    f"Tool '{tname}' result truncated for LLM: {len(result)} -> {max_chars} chars"
                )
            else:
                context_result = result

            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": context_result,
                }
            )

            # Auto-fetch resource URIs in the tool result.
            # Visualization URIs (large HTML) are fetched here and go into
            # ui_blocks — they do NOT go into chat_msgs.
            if self.valves.AUTO_FETCH_RESOURCE_URIS and status == 0:
                auto = await self._auto_fetch_resource_uris(
                    result, mgr, fetched_uris, emitter, skip_visualization=False
                )
                for uri, content, mime in auto:
                    ui_blocks.append(_render_resource(uri, content, mime))

        return tool_messages

    # ── Prompt invocation ─────────────────────────────────────────────────

    async def _handle_prompt_invocation(
        self,
        prompt_name: str,
        prompt_args: Dict[str, str],
        mgr: MCPClientManager,
        emitter,
    ) -> Optional[List[Dict]]:
        all_prompts = mgr.list_all_prompts()
        if not any(p["name"] == prompt_name for p in all_prompts):
            logger.warning(f"Prompt '{prompt_name}' not found")
            return None
        await self._emit(emitter, "info", f"Loading prompt: {prompt_name}…", False)
        status, messages = await mgr.get_prompt(prompt_name, prompt_args)
        if status != 0:
            logger.error(f"Failed to fetch prompt '{prompt_name}'")
            return None
        return messages

    # ── Main entry point ──────────────────────────────────────────────────

    async def pipe(
        self,
        body: dict,
        __user__: dict,
        __event_emitter__=None,
        __request__=None,
        __task__=None,
        __metadata__=None,
        __files__: list = None,
    ) -> str:
        messages = body.get("messages", [])
        # Open WebUI may send an empty message list when the user attaches
        # files but types no text. We still want to process those files.
        if not messages and not __files__:
            yield ""
            return

        # ── Non-chat tasks — pass-through ─────────────────────────────
        if __task__ and __task__ != "chat":
            user_obj = await Users.get_user_by_id(__user__["id"])
            resp = await generate_chat_completion(
                __request__,
                {"model": self.valves.MODEL, "messages": messages, "stream": True},
                user_obj,
            )
            if hasattr(resp, "body_iterator"):
                async for chunk in resp.body_iterator:
                    text = (
                        chunk.decode("utf-8").strip()
                        if isinstance(chunk, bytes)
                        else str(chunk)
                    )
                    if text.startswith("data: ") and text != "data: [DONE]":
                        try:
                            delta = json.loads(text[6:])["choices"][0]
                            yield delta.get("delta", {}).get("content", "")
                        except Exception:
                            pass
            return

        # ── Session management ────────────────────────────────────────
        await _cleanup_stale_sessions()
        chat_id = (__metadata__ or {}).get("chat_id", "default_chat")

        if chat_id not in GLOBAL_MCP_SESSIONS:
            await self._emit(
                __event_emitter__, "info", "Initializing MCP session…", False
            )
            mgr = MCPClientManager(
                self.valves.MCP_CONFIG,
                emit_status=lambda lvl, msg, done: self._emit(
                    __event_emitter__, lvl, msg, done
                ),
            )
            await mgr.connect_to_servers()
            GLOBAL_MCP_SESSIONS[chat_id] = CachedSession(manager=mgr)
        else:
            cached = GLOBAL_MCP_SESSIONS[chat_id]
            cached.last_used = time.time()
            mgr = cached.manager
            mgr.emit_status = lambda lvl, msg, done: self._emit(
                __event_emitter__, lvl, msg, done
            )

        try:
            # ── Upload files ──────────────────────────────────────────
            uploaded_files: List[Dict] = []
            if __files__:
                uploaded_files = await self._push_files_to_remote(__files__, __event_emitter__, chat_id)
            # Files that are available on the MCP server: newly uploaded ones
            # plus duplicates whose content was already uploaded in this chat.
            ok_uploads = [
                f
                for f in uploaded_files
                if f["status"] in ("ok", "skipped") and f.get("remote_path")
            ]
            for f in uploaded_files:
                if f["status"] == "error":
                    logger.warning(f"File upload failed: {f['name']} — {f['detail']}")
                elif f["status"] == "skipped":
                    logger.debug(f"File upload skipped (identical content exists): {f['name']} — {f['detail']}")

            # ── User-initiated prompt detection ───────────────────────
            prompt_messages: Optional[List[Dict]] = None
            last_user_msg = next(
                (m for m in reversed(messages) if m.get("role") == "user"), None
            )
            if last_user_msg:
                user_text = last_user_msg.get("content", "")
                if isinstance(user_text, str):
                    parsed_cmd = _parse_prompt_command(user_text)
                    if parsed_cmd:
                        pname, pargs = parsed_cmd
                        prompt_messages = await self._handle_prompt_invocation(
                            pname, pargs, mgr, __event_emitter__
                        )
                        if prompt_messages is not None:
                            await self._emit(
                                __event_emitter__,
                                "info",
                                f"Prompt '{pname}' applied",
                                False,
                            )

            # ── Application-controlled resource selection ─────────────
            resource_context: List[Tuple[str, str, Optional[str]]] = []
            if self.valves.AUTO_ATTACH_RESOURCES and last_user_msg:
                user_text = last_user_msg.get("content", "")
                if isinstance(user_text, str):
                    matched_uris = _match_resources_for_query(
                        user_text,
                        mgr.list_all_resources(),
                        mgr.list_all_resource_templates(),
                    )
                    if matched_uris:
                        resource_context = await self._fetch_resources_for_context(
                            matched_uris, mgr, __event_emitter__
                        )

            # ── Build system prompt ───────────────────────────────────
            sys_prompt = self._build_system_prompt(
                uploaded_files=ok_uploads or None,
                resource_context=resource_context or None,
                prompt_messages=prompt_messages,
            )

            # ── Build conversation history (context-safe) ─────────────
            #
            # Open WebUI replays the full history on every turn, including
            # assistant messages that may contain large HTML visualizations.
            # _build_history applies two guards:
            #   1. Keep only the last MAX_HISTORY_MESSAGES turns.
            #   2. Truncate any message content exceeding MAX_MESSAGE_CONTENT_CHARS.
            #
            chat_msgs: List[Dict] = [{"role": "system", "content": sys_prompt}]
            chat_msgs.extend(
                _build_history(
                    messages,
                    max_messages=self.valves.MAX_HISTORY_MESSAGES,
                    max_chars_per_msg=self.valves.MAX_MESSAGE_CONTENT_CHARS,
                )
            )

            if ok_uploads:
                file_listing = "\n".join(
                    f"- {f['name']} → remote path: {f['remote_path']}"
                    for f in ok_uploads
                )
                chat_msgs.append(
                    {
                        "role": "user",
                        "content": (
                            "[SYSTEM NOTIFICATION — not typed by the user]\n\n"
                            "The following files were uploaded to the MCP server and are ready:\n"
                            f"{file_listing}\n\n"
                            "Use these exact remote paths when calling tools.\n"
                            "[END NOTIFICATION]"
                        ),
                    }
                )

            # ── Build tool definitions ────────────────────────────────
            openai_tools = _mcp_tools_to_openai_tools(mgr.available_tools)
            payload: Dict = {
                "model": self.valves.MODEL,
                "temperature": self.valves.TEMPERATURE,
                "max_tokens": self.valves.MAX_TOKENS,
                "stream": False,
            }
            if openai_tools:
                payload["tools"] = openai_tools
                payload["tool_choice"] = "auto"

            user_obj = await Users.get_user_by_id(__user__["id"])
            ui_blocks: List[str] = []
            fetched_uris: Set[str] = {uri for uri, _, _ in resource_context}
            content = ""
            fake_retry_count = 0
            called_tools: Set[str] = set()

            # ── Agentic tool-use loop ─────────────────────────────────
            for step in range(1, self.valves.MAX_ITERATIONS + 1):
                await self._emit(
                    __event_emitter__, "info", f"Thinking (step {step})…", False
                )
                payload["messages"] = chat_msgs

                raw_resp = await generate_chat_completion(
                    __request__, payload, user_obj
                )
                resp, err = _unwrap_llm_response(raw_resp)

                if err is not None:
                    logger.error(f"LLM API error (step {step}): {err}")
                    yield (
                        f"\n\n⚠️ **LLM Error (step {step}):** {err}\n\n"
                        "_This is usually caused by the context window being exceeded. "
                        "Try reducing `MAX_HISTORY_MESSAGES` or `MAX_MESSAGE_CONTENT_CHARS` "
                        "in the pipe Valves._"
                    )
                    return

                message = resp["choices"][0]["message"]
                content = message.get("content", "") or ""
                tool_calls = _parse_tool_calls_from_response(resp)

                # ── Fake tool execution guard ─────────────────────────
                fake_tools, fake_kind = _detect_fake_tool_execution(content, tool_calls)
                if fake_tools:
                    fake_retry_count += 1
                    logger.warning(
                        f"Step {step}: model faked tool(s) {fake_tools} "
                        f"(kind={fake_kind}, retry {fake_retry_count}/{_MAX_FAKE_RETRIES})"
                    )
                    if fake_retry_count > _MAX_FAKE_RETRIES:
                        logger.error("Model keeps faking tool execution — aborting")
                        yield (
                            "\n\n⚠️ The model repeatedly generated fake tool output "
                            "instead of calling tools. Try rephrasing your request."
                        )
                        return
                    await self._emit(
                        __event_emitter__,
                        "warning",
                        f"Model faked {', '.join(fake_tools)} — retrying…",
                        False,
                    )
                    # Inject a correction into the LAST user message so the
                    # model sees the nudge without polluting history with the
                    # fake response itself.
                    tool_list = ', '.join(fake_tools)
                    if fake_kind == "raw_html":
                        correction = (
                            f"\n\n[CORRECTION: you just returned a raw HTML visualization "
                            f"instead of using the tool-calling API. "
                            f"Do NOT generate or embed HTML in your response. "
                            f"Call {tool_list} via the tool-use mechanism to produce "
                            f"the visualization — the system will render it automatically.]"
                        )
                    else:  # fake_call
                        correction = (
                            f"\n\n[CORRECTION: you just generated fake tool-call markup for "
                            f"{tool_list} instead of using the function-calling API. "
                            f"Do NOT generate <details> / <summary> HTML yourself. "
                            f"Call {tool_list} via the tool-use mechanism now.]"
                        )
                    for i in range(len(chat_msgs) - 1, -1, -1):
                        if chat_msgs[i]["role"] == "user" and isinstance(
                            chat_msgs[i].get("content"), str
                        ):
                            chat_msgs[i] = {
                                **chat_msgs[i],
                                "content": chat_msgs[i]["content"] + correction,
                            }
                            break
                    continue

                if tool_calls:
                    called_tools.update(tc["name"] for tc in tool_calls)
                    assistant_msg: Dict = {"role": "assistant", "content": content}
                    if message.get("tool_calls"):
                        assistant_msg["tool_calls"] = message["tool_calls"]
                    chat_msgs.append(assistant_msg)

                    tool_response_msgs = await self._execute_tool_calls(
                        tool_calls, mgr, ui_blocks, fetched_uris, __event_emitter__
                    )
                    chat_msgs.extend(tool_response_msgs)
                    continue
                else:
                    break
            else:
                logger.warning(
                    f"Agentic loop hit max iterations ({self.valves.MAX_ITERATIONS})"
                )
                await self._emit(
                    __event_emitter__, "warning", "Max iterations reached.", False
                )

            # ── Post-loop: auto-generate visualization if needed ───────
            _VIZ_TRIGGER_TOOLS = {
                "cminer_mine_patterns",
                "cminer_get_pattern_info",
                "cminer_filter_patterns",
                "cminer_highlight_pattern",

                "cml2graph_load_models",
                "cml2graph_select_model",
                "cml2graph_remove_models",
                "cml2graph_clear_models",
                "cml2graph_filter_models",
                "cml2graph_reset_filters_models",
                "cml2graph_load_dataset",
                "session_load",
            }
            if (
                called_tools & _VIZ_TRIGGER_TOOLS
                and "cminer_generate_visualization" not in called_tools
            ):
                logger.info(
                    "Auto-triggering cminer_generate_visualization "
                    f"(viz-affecting tools called: {called_tools & _VIZ_TRIGGER_TOOLS})"
                )
                await self._emit(
                    __event_emitter__, "info",
                    "Updating visualization…", False,
                )
                viz_tc = [{
                    "name": "cminer_generate_visualization",
                    "arguments": {},
                    "id": f"auto_viz_{int(time.time())}",
                }]
                viz_msgs = await self._execute_tool_calls(
                    viz_tc, mgr, ui_blocks, fetched_uris, __event_emitter__
                )
                # Add to chat context so the LLM's final answer can reference it
                chat_msgs.extend(viz_msgs)

            # ── Post-loop: auto-fetch URIs in LLM final answer ────────
            # Skip visualization URIs here — they were already fetched
            # during tool execution and are in ui_blocks.
            if self.valves.AUTO_FETCH_RESOURCE_URIS and content:
                post_fetched = await self._auto_fetch_resource_uris(
                    content,
                    mgr,
                    fetched_uris,
                    __event_emitter__,
                    skip_visualization=True,  # never re-fetch huge HTML into the answer
                )
                for uri, rc, rm in post_fetched:
                    ui_blocks.append(_render_resource(uri, rc, rm))

            # ── Strip internal URIs from final answer ─────────────────
            if self.valves.STRIP_INTERNAL_URIS and content:
                content = _strip_internal_uris(content)

            # ── Render resource context blocks ────────────────────────
            for uri, rc, rm in resource_context:
                ui_blocks.append(_render_resource(uri, rc, rm))

            # ── Yield UI blocks then final answer ─────────────────────
            for block in ui_blocks:
                yield block
            yield content

        finally:
            await self._emit(__event_emitter__, "info", "Done", True)
