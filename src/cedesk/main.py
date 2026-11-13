"""The CeDesk API application."""

import asyncio
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

from cedesk import logs
from cedesk.cevanos_client import make_cevanos
from cedesk.db import make_engine, make_sessionmaker
from cedesk.dnscheck import DnsLookup
from cedesk.helpdesk.api import api_keys as api_keys_api
from cedesk.helpdesk.api import apps as apps_api
from cedesk.helpdesk.api import articles as articles_api
from cedesk.helpdesk.api import attachments as attachments_api
from cedesk.helpdesk.api import audit as audit_api
from cedesk.helpdesk.api import auth as auth_api
from cedesk.helpdesk.api import catalog as catalog_api
from cedesk.helpdesk.api import cevanos as cevanos_api
from cedesk.helpdesk.api import demo as demo_api
from cedesk.helpdesk.api import email as email_api
from cedesk.helpdesk.api import email_in as email_in_api
from cedesk.helpdesk.api import imports as imports_api
from cedesk.helpdesk.api import inbound_api as inbound_api_api
from cedesk.helpdesk.api import jira as jira_api
from cedesk.helpdesk.api import limits as limits_api
from cedesk.helpdesk.api import macros as macros_api
from cedesk.helpdesk.api import members as members_api
from cedesk.helpdesk.api import messages as messages_api
from cedesk.helpdesk.api import outbox as outbox_api
from cedesk.helpdesk.api import portal as portal_api
from cedesk.helpdesk.api import privacy as privacy_api
from cedesk.helpdesk.api import public as public_api
from cedesk.helpdesk.api import reports as reports_api
from cedesk.helpdesk.api import roles as roles_api
from cedesk.helpdesk.api import routing as routing_api
from cedesk.helpdesk.api import rules as rules_api
from cedesk.helpdesk.api import sandbox as sandbox_api
from cedesk.helpdesk.api import saved_replies as saved_replies_api
from cedesk.helpdesk.api import security as security_api
from cedesk.helpdesk.api import side_conversations as side_conversations_api
from cedesk.helpdesk.api import sla as sla_api
from cedesk.helpdesk.api import sso as sso_api
from cedesk.helpdesk.api import stream as stream_api
from cedesk.helpdesk.api import ticket_tools as ticket_tools_api
from cedesk.helpdesk.api import tickets as tickets_api
from cedesk.helpdesk.api import triage as triage_api
from cedesk.helpdesk.api import two_factor as two_factor_api
from cedesk.helpdesk.api import webhooks as webhooks_api
from cedesk.helpdesk.domain import limits as limit_rules
from cedesk.helpdesk.service import apps as apps_service
from cedesk.helpdesk.service import jira as jira_service
from cedesk.helpdesk.service import limits as limits_service
from cedesk.helpdesk.service.errors import ServiceError, TooManyAttempts
from cedesk.helpdesk.service.imports.framework import make_importers
from cedesk.helpdesk.service.stream import Slots
from cedesk.ratelimit import SlidingWindowLimiter
from cedesk.settings import Edition, Settings, get_settings
from cedesk.storage import make_store
from cedesk.webhook_client import WebhookClient

logger = logging.getLogger("cedesk")
READY_TIMEOUT_SECONDS = 2.0

HTTP_CODES = {
    401: "NotSignedIn",
    403: "Forbidden",
    404: "NotFound",
    405: "MethodNotAllowed",
    409: "Conflict",
    422: "Invalid",
    429: "TooManyAttempts",
    503: "Unavailable",
}
# A caller's X-Request-ID is reused only when it looks like an id, never free text.
REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
# Deadlock, serialization failure, lock not available: losing a race, safe to retry.
RETRYABLE_SQLSTATES = {"40P01", "40001", "55P03"}


def _error(
    status: int,
    code: str,
    message: str,
    *,
    details: list[dict[str, str]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, object] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status, headers=headers)


def _template(path: str, params: Mapping[str, Any]) -> str:
    """`/api/v1/tickets/9f1c.../messages` -> `/api/v1/tickets/{ticket_id}/messages`, so a
    log line names the route without any value taken from the URL."""
    parts = path.split("/")
    names = {str(value): name for name, value in params.items()}
    return "/".join("{" + names[p] + "}" if p in names else p for p in parts)


def _uuid_or_none(value: object) -> str | None:
    """Only a real id is logged; `/tickets/jane@example.com` logs no ticket_id."""
    try:
        return str(uuid.UUID(str(value))) if value is not None else None
    except ValueError:
        return None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    problems = settings.unsafe()
    if problems:
        raise RuntimeError(
            f"Refusing to start with CEDESK_ENV={settings.env.value}: " + "; ".join(problems)
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Here, not only in `cedesk serve`: with --reload the app runs in a child process
        # that never sees the parent's logging setup.
        logs.configure(settings.log_level)
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="CeDesk API",
        version="0.1.0",
        description="Help desk backend: tickets, routing, SLA, macros and rules.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    # Built now, so a storage setting that is missing stops the app at start-up.
    app.state.store = make_store(settings)
    app.state.cevanos = make_cevanos(settings)  # the connector; each workspace's key is in the db
    app.state.dns = DnsLookup()  # the support address DNS check (md C10)
    app.state.stream_slots = Slots()  # open live streams per person (md C11)
    # Imports from other help desks (md section 8); tests replace `build` with a fake source.
    app.state.importers = make_importers(settings.secret_key.get_secret_value(), app.state.store)
    app.state.attempts = SlidingWindowLimiter(
        settings.sign_in_limit, settings.sign_in_window_seconds
    )
    # The public web form has its own budget, so form posts never use up sign-ins.
    app.state.form_attempts = SlidingWindowLimiter(
        settings.form_limit, settings.form_window_seconds
    )
    app.state.vote_attempts = SlidingWindowLimiter(
        settings.vote_limit, settings.vote_window_seconds
    )
    app.state.public_attempts = SlidingWindowLimiter(
        settings.public_limit, settings.public_window_seconds
    )
    # Requests with API keys, per workspace (md PL2).
    app.state.api_key_requests = SlidingWindowLimiter(settings.api_key_limit, 60)
    # Outgoing webhooks: checks a URL's host when it is saved, sends the test ping (md PL3).
    app.state.webhook_client = WebhookClient()
    # Marketplace apps: how the Shopify and Stripe cards reach the provider (md PL7, C12).
    app.state.app_http = apps_service.make_http
    # ...and how Jira issues are made and the Slack test line is sent (md X10, W20).
    app.state.app_post = jira_service.make_post
    # Cloud plan limits (md O4): only on the cloud edition, with the plans from config.
    limits_service.configure(
        cloud=settings.edition is Edition.cloud, plans=limit_rules.parse_plans(settings.plans)
    )

    @app.middleware("http")
    async def request_log(request: Request, call_next: Any) -> Response:
        """One log line per request with its ids and timing (md 2B). The route template
        (`/api/v1/tickets/{ticket_id}`) is logged, not the URL, so query strings such as
        search text never reach the logs."""
        logs.reset()
        given = request.headers.get("x-request-id", "")
        request_id = given if REQUEST_ID.match(given) else uuid.uuid4().hex
        logs.bind(request_id=request_id)
        started = time.perf_counter()
        status = 500
        try:
            response: Response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            params = request.scope.get("path_params") or {}
            logs.bind(ticket_id=_uuid_or_none(params.get("ticket_id")))
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "route": _template(request.url.path, params)
                    if request.scope.get("route") is not None
                    else "(unmatched)",  # an unknown URL can hold anything, even an email
                    "status": status,
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )

    # Every error has one shape (README, "Errors"):
    #   {"error": {"code": "...", "message": "...", "details": [...]}}
    # `details` appears only on validation errors, one entry per field.

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        if isinstance(exc, TooManyAttempts):
            headers = {"Retry-After": str(exc.retry_after)}
        return _error(exc.status, type(exc).__name__, exc.message, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # The submitted values are left out: they can hold passwords or customer data.
        details = [
            {
                "field": ".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]),
                "location": str(e["loc"][0]),
                "message": e["msg"],
            }
            for e in exc.errors()
        ]
        return _error(422, "Invalid", "Some fields are not valid.", details=details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = HTTP_CODES.get(exc.status_code, "Error")
        return _error(exc.status_code, code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(DBAPIError)
    async def database_error(request: Request, exc: DBAPIError) -> JSONResponse:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        if sqlstate in RETRYABLE_SQLSTATES:
            logger.warning("request lost a database race: %s", sqlstate)
            return _error(409, "Conflict", "Someone changed this at the same moment. Try again.")
        logger.error("database error: %s %s", type(exc.orig).__name__, sqlstate)
        return _error(500, "Internal", "Something went wrong.")

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error: %s", type(exc).__name__)
        return _error(500, "Internal", "Something went wrong.")

    origins = settings.allowed_origins()
    if origins:
        # Added last, so it is the outermost layer and error answers carry the headers too.
        # Bearer tokens, not cookies: no credentials mode needed.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "If-Match",
                "Idempotency-Key",
                "Last-Event-ID",
                "X-Request-ID",
            ],
            expose_headers=["ETag", "X-Request-ID", "Retry-After", "Idempotent-Replayed"],
            max_age=600,
        )

    app.include_router(auth_api.router, prefix="/api/v1")
    app.include_router(members_api.router, prefix="/api/v1")
    app.include_router(roles_api.router, prefix="/api/v1")
    app.include_router(security_api.router, prefix="/api/v1")
    app.include_router(sso_api.router, prefix="/api/v1")
    app.include_router(audit_api.router, prefix="/api/v1")
    app.include_router(privacy_api.router, prefix="/api/v1")
    app.include_router(sandbox_api.router, prefix="/api/v1")
    app.include_router(two_factor_api.router, prefix="/api/v1")
    # Before tickets: `/tickets/from-conversation/{id}` must not be read as a ticket id.
    app.include_router(cevanos_api.router, prefix="/api/v1")
    app.include_router(tickets_api.router, prefix="/api/v1")
    app.include_router(ticket_tools_api.router, prefix="/api/v1")
    app.include_router(catalog_api.router, prefix="/api/v1")
    app.include_router(messages_api.router, prefix="/api/v1")
    app.include_router(routing_api.router, prefix="/api/v1")
    app.include_router(attachments_api.router, prefix="/api/v1")
    app.include_router(saved_replies_api.router, prefix="/api/v1")
    app.include_router(rules_api.router, prefix="/api/v1")
    app.include_router(macros_api.router, prefix="/api/v1")
    app.include_router(email_api.router, prefix="/api/v1")
    app.include_router(email_in_api.router, prefix="/api/v1")
    app.include_router(sla_api.router, prefix="/api/v1")
    app.include_router(outbox_api.router, prefix="/api/v1")
    app.include_router(portal_api.router, prefix="/api/v1")
    app.include_router(public_api.router, prefix="/api/v1")
    app.include_router(articles_api.router, prefix="/api/v1")
    app.include_router(triage_api.router, prefix="/api/v1")
    app.include_router(reports_api.router, prefix="/api/v1")
    app.include_router(imports_api.router, prefix="/api/v1")
    app.include_router(stream_api.router, prefix="/api/v1")
    app.include_router(api_keys_api.router, prefix="/api/v1")
    app.include_router(inbound_api_api.router, prefix="/api/v1")
    app.include_router(webhooks_api.router, prefix="/api/v1")
    app.include_router(apps_api.router, prefix="/api/v1")
    app.include_router(jira_api.router, prefix="/api/v1")
    app.include_router(side_conversations_api.router, prefix="/api/v1")
    app.include_router(limits_api.router, prefix="/api/v1")
    app.include_router(demo_api.router, prefix="/api/v1")

    @app.get("/healthz", tags=["health"], summary="The process is up")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/readyz",
        tags=["health"],
        summary="The database answers",
        responses={503: {"description": "The database did not answer within 2 seconds"}},
    )
    async def readyz() -> JSONResponse:
        try:
            async with asyncio.timeout(READY_TIMEOUT_SECONDS):
                async with app.state.engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
        except Exception as exc:
            # Only the error type is logged: driver messages can carry host details.
            logger.warning("readiness check failed: %s", type(exc).__name__)
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok", "edition": settings.edition.value})

    return app


app = create_app()
