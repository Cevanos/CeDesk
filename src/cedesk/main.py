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
from cedesk.captcha import Turnstile
from cedesk.cevanos_client import make_cevanos
from cedesk.db import make_engine, make_sessionmaker
from cedesk.dnscheck import DnsLookup
from cedesk.helpdesk.api import ai as ai_api
from cedesk.helpdesk.api import api_keys as api_keys_api
from cedesk.helpdesk.api import apps as apps_api
from cedesk.helpdesk.api import archive as archive_api
from cedesk.helpdesk.api import article_translations as article_translations_api
from cedesk.helpdesk.api import articles as articles_api
from cedesk.helpdesk.api import attachments as attachments_api
from cedesk.helpdesk.api import audit as audit_api
from cedesk.helpdesk.api import auth as auth_api
from cedesk.helpdesk.api import auto_close as auto_close_api
from cedesk.helpdesk.api import backlog as backlog_api
from cedesk.helpdesk.api import campaigns as campaigns_api
from cedesk.helpdesk.api import catalog as catalog_api
from cedesk.helpdesk.api import cevanos as cevanos_api
from cedesk.helpdesk.api import contact_fields as contact_fields_api
from cedesk.helpdesk.api import contacts as contacts_api
from cedesk.helpdesk.api import dashboards as dashboards_api
from cedesk.helpdesk.api import demo as demo_api
from cedesk.helpdesk.api import email as email_api
from cedesk.helpdesk.api import email_in as email_in_api
from cedesk.helpdesk.api import flows as flows_api
from cedesk.helpdesk.api import help_domain as help_domain_api
from cedesk.helpdesk.api import imports as imports_api
from cedesk.helpdesk.api import inbound_api as inbound_api_api
from cedesk.helpdesk.api import inbox as inbox_api
from cedesk.helpdesk.api import jira as jira_api
from cedesk.helpdesk.api import limits as limits_api
from cedesk.helpdesk.api import live as live_api
from cedesk.helpdesk.api import macros as macros_api
from cedesk.helpdesk.api import members as members_api
from cedesk.helpdesk.api import messages as messages_api
from cedesk.helpdesk.api import mobile as mobile_api
from cedesk.helpdesk.api import outbox as outbox_api
from cedesk.helpdesk.api import portal as portal_api
from cedesk.helpdesk.api import privacy as privacy_api
from cedesk.helpdesk.api import public as public_api
from cedesk.helpdesk.api import push as push_api
from cedesk.helpdesk.api import report_engine as report_engine_api
from cedesk.helpdesk.api import report_schedules as report_schedules_api
from cedesk.helpdesk.api import reports as reports_api
from cedesk.helpdesk.api import roles as roles_api
from cedesk.helpdesk.api import routing as routing_api
from cedesk.helpdesk.api import rules as rules_api
from cedesk.helpdesk.api import sandbox as sandbox_api
from cedesk.helpdesk.api import saved_replies as saved_replies_api
from cedesk.helpdesk.api import saved_reports as saved_reports_api
from cedesk.helpdesk.api import security as security_api
from cedesk.helpdesk.api import segments as segments_api
from cedesk.helpdesk.api import side_conversations as side_conversations_api
from cedesk.helpdesk.api import signup as signup_api
from cedesk.helpdesk.api import sla as sla_api
from cedesk.helpdesk.api import sso as sso_api
from cedesk.helpdesk.api import stream as stream_api
from cedesk.helpdesk.api import ticket_tools as ticket_tools_api
from cedesk.helpdesk.api import tickets as tickets_api
from cedesk.helpdesk.api import triage as triage_api
from cedesk.helpdesk.api import two_factor as two_factor_api
from cedesk.helpdesk.api import views as views_api
from cedesk.helpdesk.api import webhooks as webhooks_api
from cedesk.helpdesk.domain import bulk as bulk_rules
from cedesk.helpdesk.domain import limits as limit_rules
from cedesk.helpdesk.domain import transcript as transcript_rules
from cedesk.helpdesk.service import apps as apps_service
from cedesk.helpdesk.service import jira as jira_service
from cedesk.helpdesk.service import limits as limits_service
from cedesk.helpdesk.service.errors import ServiceError, TooManyAttempts
from cedesk.helpdesk.service.imports.framework import make_importers, retry_policy
from cedesk.helpdesk.service.live import LiveCache
from cedesk.helpdesk.service.report_engine import ReportSlots
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


_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_HSTS = "max-age=31536000; includeSubDomains"
_DOC_PATHS = ("/docs", "/redoc")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    problems = settings.unsafe() + settings.signup_problems()
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
    # Report queries run at once in this process (plan 3.2); more wait, then answer 429.
    app.state.report_slots = ReportSlots(settings.report_concurrency)
    app.state.live_cache = LiveCache()  # the live view's answer, shared for a few seconds
    # Imports from other help desks (md section 8); tests replace `build` with a fake source.
    app.state.importers = make_importers(
        settings.secret_key.get_secret_value(), app.state.store, retry=retry_policy(settings)
    )
    app.state.attempts = SlidingWindowLimiter(
        settings.sign_in_limit, settings.sign_in_window_seconds
    )
    # Wrong keys on Caddy's TLS check (plan 4.2) have their own budget, so a caller guessing
    # the key can never use up anyone's sign-ins.
    app.state.tls_attempts = SlidingWindowLimiter(
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
    # Campaigns (plan phase 6): one-click unsubscribes and open images come from mail
    # providers' few addresses, so they get their own, larger budget; test emails per Admin.
    app.state.unsubscribe_attempts = SlidingWindowLimiter(
        settings.unsubscribe_limit, settings.unsubscribe_window_seconds
    )
    app.state.campaign_tests = SlidingWindowLimiter(campaigns_api.TESTS_PER_HOUR, 3600)
    # Inbox tools (feature-gap plan 1.3, 1.6): bulk changes per person; transcripts per
    # person and per workspace (each one is an email out of the help desk).
    app.state.bulk_requests = SlidingWindowLimiter(bulk_rules.PER_MINUTE, 60)
    app.state.transcript_requests = SlidingWindowLimiter(transcript_rules.PER_HOUR, 3600)
    app.state.transcript_workspace = SlidingWindowLimiter(transcript_rules.PER_HOUR_WORKSPACE, 3600)
    # Outgoing webhooks: checks a URL's host when it is saved, sends the test ping (md PL3).
    app.state.webhook_client = WebhookClient()
    # Marketplace apps: how the Shopify and Stripe cards reach the provider (md PL7, C12).
    app.state.app_http = apps_service.make_http
    # ...and how Jira issues are made and the Slack test line is sent (md X10, W20).
    app.state.app_post = jira_service.make_post
    # Cloud plan limits (md O4): only on the cloud edition, with the plans from config.
    plans = limit_rules.parse_plans(settings.plans)
    limits_service.configure(cloud=settings.edition is Edition.cloud, plans=plans)
    # Self-service sign-up: a default plan that doesn't exist stops the app here.
    if (
        settings.signup_enabled
        and settings.edition is Edition.cloud
        and settings.signup_default_plan
        and settings.signup_default_plan not in plans
    ):
        raise RuntimeError(
            f"CEDESK_SIGNUP_DEFAULT_PLAN={settings.signup_default_plan!r} is not one of the "
            f"plans: {', '.join(sorted(plans))}"
        )
    secret = settings.signup_captcha_secret
    app.state.captcha = Turnstile(secret.get_secret_value()) if secret else None
    # Sign-up passwords hashed at once (each Argon2 hash takes about 64 MB).
    app.state.signup_hash_slots = asyncio.Semaphore(settings.signup_hash_slots)

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

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        """Browser protections on every answer (md S: security headers). The API answers
        JSON, files and redirects, never pages, so nothing may frame it, run scripts from it
        or learn where a link came from. A header a route set itself (a file's sandbox CSP)
        is kept. The interactive docs (/docs, /redoc) load their own scripts, so they get
        all but the CSP. HSTS only over https: on plain http browsers ignore it anyway."""
        response: Response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if not request.url.path.startswith(_DOC_PATHS):
            response.headers.setdefault("Content-Security-Policy", _API_CSP)
        if request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", _HSTS)
        return response

    # Every error has one shape (README, "Errors"):
    #   {"error": {"code": "...", "message": "...", "details": [...]}}
    # `details` appears only on validation errors, one entry per field.

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        if isinstance(exc, TooManyAttempts):
            headers = {"Retry-After": str(exc.retry_after)}
        return _error(exc.status, exc.code or type(exc).__name__, exc.message, headers=headers)

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
    app.include_router(signup_api.router, prefix="/api/v1")
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
    app.include_router(inbox_api.router, prefix="/api/v1")  # before /tickets/{id}
    app.include_router(tickets_api.router, prefix="/api/v1")
    app.include_router(views_api.router, prefix="/api/v1")
    app.include_router(contacts_api.router, prefix="/api/v1")
    app.include_router(contact_fields_api.router, prefix="/api/v1")
    app.include_router(segments_api.router, prefix="/api/v1")
    app.include_router(campaigns_api.router, prefix="/api/v1")
    app.include_router(campaigns_api.public, prefix="/api/v1")  # before /public/{workspace}
    app.include_router(auto_close_api.router, prefix="/api/v1")
    app.include_router(ticket_tools_api.router, prefix="/api/v1")
    app.include_router(catalog_api.router, prefix="/api/v1")
    app.include_router(messages_api.router, prefix="/api/v1")
    app.include_router(routing_api.router, prefix="/api/v1")
    app.include_router(attachments_api.router, prefix="/api/v1")
    app.include_router(saved_replies_api.router, prefix="/api/v1")
    app.include_router(rules_api.router, prefix="/api/v1")
    app.include_router(flows_api.router, prefix="/api/v1")
    app.include_router(macros_api.router, prefix="/api/v1")
    app.include_router(email_api.router, prefix="/api/v1")
    app.include_router(email_in_api.router, prefix="/api/v1")
    app.include_router(sla_api.router, prefix="/api/v1")
    app.include_router(outbox_api.router, prefix="/api/v1")
    app.include_router(portal_api.router, prefix="/api/v1")
    app.include_router(public_api.router, prefix="/api/v1")
    app.include_router(articles_api.router, prefix="/api/v1")
    app.include_router(article_translations_api.router, prefix="/api/v1")
    app.include_router(help_domain_api.router, prefix="/api/v1")
    if settings.tls_ask_key:  # custom help center domains are off until the key is set
        app.include_router(help_domain_api.internal_router, prefix="/api/v1")
    app.include_router(triage_api.router, prefix="/api/v1")
    app.include_router(reports_api.router, prefix="/api/v1")
    app.include_router(backlog_api.router, prefix="/api/v1")
    app.include_router(report_engine_api.router, prefix="/api/v1")
    app.include_router(saved_reports_api.router, prefix="/api/v1")
    app.include_router(dashboards_api.router, prefix="/api/v1")
    app.include_router(live_api.router, prefix="/api/v1")
    app.include_router(report_schedules_api.router, prefix="/api/v1")
    app.include_router(imports_api.router, prefix="/api/v1")
    app.include_router(stream_api.router, prefix="/api/v1")
    app.include_router(mobile_api.router, prefix="/api/v1")
    app.include_router(api_keys_api.router, prefix="/api/v1")
    app.include_router(inbound_api_api.router, prefix="/api/v1")
    app.include_router(webhooks_api.router, prefix="/api/v1")
    app.include_router(apps_api.router, prefix="/api/v1")
    app.include_router(jira_api.router, prefix="/api/v1")
    app.include_router(side_conversations_api.router, prefix="/api/v1")
    app.include_router(limits_api.router, prefix="/api/v1")
    app.include_router(demo_api.router, prefix="/api/v1")
    app.include_router(archive_api.router, prefix="/api/v1")
    app.include_router(push_api.router, prefix="/api/v1")
    app.include_router(ai_api.router, prefix="/api/v1")

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
