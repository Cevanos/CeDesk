"""CORS for the paths AI apps call (/mcp, the OAuth token, registration and revocation
endpoints, and the discovery documents): any origin, since they take bearer tokens or
client credentials, never cookies. Every other path keeps the app's own CORS list."""

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

_EXACT = frozenset({"/mcp", "/oauth/token", "/oauth/register", "/oauth/revoke"})
_PREFIX = "/.well-known/"


def is_ai_path(path: str) -> bool:
    return path in _EXACT or path.startswith(_PREFIX)


class AiAppCors:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.cors = CORSMiddleware(
            app,
            allow_origins=["*"],
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Mcp-Protocol-Version",
                "Mcp-Session-Id",
                "Last-Event-ID",
            ],
            expose_headers=["Mcp-Session-Id", "WWW-Authenticate"],
            max_age=600,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and is_ai_path(scope["path"]):
            await self.cors(scope, receive, send)
        else:
            await self.app(scope, receive, send)
