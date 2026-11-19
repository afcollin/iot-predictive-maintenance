"""Opstart af Asset Condition Monitoring API.

Kør lokalt:   uvicorn app.main:create_app --factory --reload
Swagger:      http://localhost:8000/docs
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import __version__, metrics
from app.api import router
from app.config import Settings
from app.domain import (
    AlarmAlreadyAcknowledged,
    AlarmNotFound,
    DomainError,
    ImplausibleSensorValue,
    ThresholdPolicy,
    UnknownMetric,
    new_id,
)
from app.logging_setup import configure_logging
from app.notifier import LogNotifier, WebhookNotifier
from app.persistence import (
    SqlMonitoringRepository,
    create_db_engine,
    create_session_factory,
    database_is_healthy,
    init_db,
)
from app.schemas import HealthOut
from app.security import RateLimiter, client_ip
from app.service import Notifier

logger = logging.getLogger("app")

DESCRIPTION = """
MVP for **Intelligent IoT Solutions A/S**, bounded context **Asset Condition Monitoring** (core subdomain).

Flowet følger Event Storming fra delopgave 1:

1. En IoT-enhed sender en måling → event **SensorValueReceived**
2. Threshold-check mod tærskelreglen for målepunktet → beslutning
3. Overskrides tærsklen → event **AnomalyDetected**
4. Der oprettes eller opdateres en **alarm**, og kontrolcentret notificeres → handling

Alle endpoints under `/api/v1` kræver headeren `X-API-Key`. Klik på **Authorize** for at angive den.
"""

_ERROR_STATUS: dict[type[DomainError], int] = {
    UnknownMetric: 422,
    ImplausibleSensorValue: 422,
    AlarmNotFound: 404,
    AlarmAlreadyAcknowledged: 409,
}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9\-]{1,64}$")

# Tilføjet efter DAST-scanning (OWASP ZAP fandt manglende X-Content-Type-Options og Cross-Origin-Resource-Policy)
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",  # browseren må ikke gætte indholdstypen
    "Cross-Origin-Resource-Policy": "same-origin",  # andre websites må ikke indlejre svarene
    "X-Frame-Options": "DENY",  # Swagger-siden må ikke vises i en iframe (clickjacking)
}


def create_app(settings: Settings | None = None, notifier: Notifier | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)

    engine = create_db_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_db(engine)
        logger.info(f"Asset Condition Monitoring API {__version__} startet")
        yield
        engine.dispose()

    app = FastAPI(
        title="Asset Condition Monitoring API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = create_session_factory(engine)
    app.state.policy = ThresholdPolicy()
    if notifier is None:
        notifier = WebhookNotifier(settings.notify_webhook_url) if settings.notify_webhook_url else LogNotifier()
    app.state.notifier = notifier
    app.state.rate_limiter = (
        RateLimiter(settings.rate_limit_per_minute) if settings.rate_limit_per_minute > 0 else None
    )

    @app.middleware("http")
    async def audit_log(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """Audit-log: hvert kald logges med tid, klient, endpoint, statuskode og varighed. Aldrig API-nøgler."""
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID.match(incoming) else new_id()
        request.state.request_id = request_id
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            _observe_http(request, 500, start)  # uventede fejl håndteres længere ude, men skal tælles med
            raise
        _observe_http(request, response.status_code, start)
        response.headers["X-Request-ID"] = request_id
        response.headers.update(_SECURITY_HEADERS)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"  # måledata og alarmer må ikke caches undervejs
        if request.url.path in ("/health", "/metrics") and response.status_code == 200:
            # health probes (hvert 10. s) og Prometheus (hvert 5. s) ville ellers drukne de rigtige kald i loggen
            return response
        logger.info(
            "HTTP-request",
            extra={
                "request_id": request_id,
                "client_ip": client_ip(request),
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000, 1),
            },
        )
        return response

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        """Fejl sikkert: detaljer i loggen, kun en generisk besked og et request-id til klienten."""
        request_id = getattr(request.state, "request_id", None) or new_id()
        logger.error(
            "Uventet fejl",
            exc_info=exc,
            extra={"request_id": request_id, "path": request.url.path, "status_code": 500},
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "InternalServerError",
                "detail": "Der opstod en intern fejl. Kontakt driften og oplys request_id.",
                "request_id": request_id,
            },
            headers={"X-Request-ID": request_id},
        )

    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        status_code = next((code for cls, code in _ERROR_STATUS.items() if isinstance(exc, cls)), 400)
        if isinstance(exc, (UnknownMetric, ImplausibleSensorValue)):
            metrics.SENSOR_VALUES_REJECTED.labels(reason=type(exc).__name__).inc()  # mulig sensorfejl
        logger.warning(
            str(exc), extra={"path": request.url.path, "status_code": status_code, "error": type(exc).__name__}
        )
        return JSONResponse(status_code=status_code, content={"error": type(exc).__name__, "detail": str(exc)})

    @app.get("/health", response_model=HealthOut, tags=["Drift"], summary="Health check til monitorering")
    def health(response: Response) -> HealthOut:
        healthy = database_is_healthy(app.state.session_factory)
        if not healthy:
            response.status_code = 503
        return HealthOut(
            status="ok" if healthy else "degraded",
            database="ok" if healthy else "unavailable",
            version=__version__,
        )

    if settings.metrics_enabled:

        @app.get("/metrics", include_in_schema=False)
        def prometheus_metrics() -> Response:
            """Prometheus henter (scraper) målingerne herfra. Antal åbne alarmer læses fra databasen."""
            with app.state.session_factory() as session:
                metrics.set_open_alarms(SqlMonitoringRepository(session).count_open_alarms_by_severity())
            return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(router)
    return app


def _observe_http(request: Request, status_code: int, start: float) -> None:
    if request.url.path == "/metrics":
        return
    # Endpoint-skabelonen (fx /api/v1/alarms/{alarm_id}) i stedet for den konkrete sti, så antallet af
    # tidsserier ikke vokser med hvert alarm-id. Ukendte stier (404) samles under "unmatched".
    route = getattr(request.scope.get("route"), "path", None)
    if route is None:
        route = "unmatched" if status_code == 404 else request.url.path  # fx /docs og /openapi.json
    metrics.HTTP_REQUESTS.labels(method=request.method, route=route, status=str(status_code)).inc()
    metrics.HTTP_DURATION.labels(method=request.method, route=route).observe(time.perf_counter() - start)
