from typing import Annotated
from fastmcp import FastMCP, Context
from fastmcp.server.dependencies import get_access_token
from app.core.state import SESSIONS, session_store, get_session
from app.utils.logger import setup_logging

logger = setup_logging("DEBUG", __name__)
mcp = FastMCP("session_management")


@mcp.tool()
async def session_save(
    name: Annotated[str, "name for this session, to identify it later"] = None,
    description: Annotated[str, "Description of what was mined/analyzed, the models and the patterns, to understand the context, max 255 characters"] = None,
    ctx: Context = None,
) -> str:
    """
    Save the current session (models, patterns) for later use.
    Use this after mining patterns so you can restore them in a future chat.
    """
    if not ctx or not ctx.session_id:
        return "Error: No session ID in context."

    client_id = get_access_token().client_id or "anonymous"
    session = get_session(ctx)
    session_id = ctx.session_id

    if not session.get("patterns") and not session.get("models"):
        return "Nothing to save. Load models and/or mine patterns first."

    try:
        saved_path = session_store.save_session(
            client_id=client_id,
            session_id=session_id,
            session_data=session,
            name=name,
            description=description
        )

        num_models = len(session.get("models", {}))
        num_patterns = len(session.get("patterns", []))

        return "Session saved successfully"
    except Exception as e:
        logger.exception(f"Failed to save session {session_id}")
        return f"Error saving session: {e}"


@mcp.tool()
async def session_load(
    session_id: Annotated[str, "The session ID to load"],
    ctx: Context = None,
) -> str:
    """
    Load a previously saved session into your current context.
    This restores all models and patterns.
    To obtain a session ID, use the session_list tool
    WARNING: This will replace your current session data.
    """
    if not ctx or not ctx.session_id:
        return "Error: No session ID in context."

    client_id = get_access_token().client_id or "anonymous"
    saved_session = session_store.load_session(client_id, session_id)

    if saved_session is None:
        return (
            f"Session '{session_id}' not found for your account. "
            f"Use session_list to see your available sessions."
        )

    # Replace current session
    current_session = get_session(ctx)
    current_session.clear()
    current_session.update(saved_session)

    num_models = len(saved_session.get("models", {}))
    num_patterns = len(saved_session.get("patterns", []))
    model_names = list(saved_session.get("models", {}).keys())

    summary = (
        f"Session '{session_id}' loaded successfully\n\n"
        f"Restored: {num_models} model(s), {num_patterns} pattern(s)\n"
    )

    #if model_names:
    #    summary += f"\nLoaded models:\n" + "\n".join(f"  - {m}" for m in model_names)

    #if num_patterns > 0:
    #    summary += (
    #        f"\n\nPatterns are ready to query. Use:\n"
    #        f"  - cminer_list_patterns\n"
    #        f"  - cminer_get_pattern_info\n"
    #        f"  - cminer_filter_patterns\n"
    #        f"  - cminer_generate_visualization (to regenerate the viz)"
    #    )
    return summary


@mcp.tool()
async def session_list(ctx: Context = None) -> str:
    """
    List all your saved sessions with their metadata.
    Use this to find sessions you want to load.
    And get their session IDs to load them.
    """
    if not ctx:
        return "Error: No context available."

    client_id = get_access_token().client_id or "anonymous"
    sessions = session_store.list_sessions(client_id)

    if not sessions:
        return (
            f"No saved sessions found for client '{client_id}'.\n"
            f"Use session_save to save your current work."
        )

    lines = [f"Found {len(sessions)} saved session(s) for {client_id}:\n"]

    for i, s in enumerate(sessions, 1):
        lines.append(
            f"{i}. {s['name']}\n"
            f"   ID: {s['session_id']}\n"
            f"   Saved: {s['saved_at']}\n"
            f"   Data: {s['num_models']} model(s), {s['num_patterns']} pattern(s)"
        )
        if s.get('description'):
            lines.append(f"   Description: {s['description']}")
        lines.append("")

    lines.append("Use session_load with the session ID to restore a session.")
    return "\n".join(lines)


@mcp.tool()
async def session_info(
    session_id: Annotated[str, "The session ID to get info about"],
    ctx: Context = None,
) -> str:
    """Get detailed information about a saved session without loading it."""
    if not ctx:
        return "Error: No context available."

    client_id = get_access_token().client_id or "anonymous"
    info = session_store.get_session_info(client_id, session_id)

    if info is None:
        return f"Session '{session_id}' not found for your account."

    return (
        f"Session: {info['name']}\n"
        f"ID: {info['session_id']}\n"
        f"Client: {info['client_id']}\n"
        f"Created: {info.get('created_at', 'unknown')}\n"
        f"Saved: {info['saved_at']}\n"
        f"Models: {info['num_models']}\n"
        f"Patterns: {info['num_patterns']}\n"
        f"Active model: {info.get('active_model', 'none')}\n"
        f"Description: {info.get('description', 'none')}\n"
    )


@mcp.tool()
async def session_delete(
    session_id: Annotated[str, "The session ID to delete"],
    ctx: Context = None,
) -> str:
    """Delete a saved session permanently."""
    if not ctx:
        return "Error: No context available."

    client_id = get_access_token().client_id or "anonymous"

    if session_store.delete_session(client_id, session_id):
        return f"Session '{session_id}' deleted."
    return f"Session '{session_id}' not found for your account."