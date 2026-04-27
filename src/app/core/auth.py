from fastmcp.server.auth.providers.debug import DebugTokenVerifier

# Development tokens
DEV_TOKENS: dict[str, str] = {
    "dev-token-admin": "admin",
    "dev-token-me": "me",
}

VALID_TOKENS: set[str] = set(DEV_TOKENS.keys())


async def validate_token(token: str) -> bool:
    """
    Validate a bearer token.
    Very basic implementation for development purposes only.
    Ideas: Redis, database, JWT validation, etc.
    """
    print(f"Validating token: {token}")
    print(f"is valid: {token in VALID_TOKENS}")
    return token in VALID_TOKENS


def create_auth_verifier() -> DebugTokenVerifier:
    """Create the auth verifier."""
    return DebugTokenVerifier(
        validate=validate_token,
        client_id="admin",  # TODO: extract client_id from token in a real implementation
        scopes=["api:access", "session:read", "session:write"],
    )