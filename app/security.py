"""Adgangskontrol og beskyttelse af API'et (defense in depth i applikationslaget).

* API-nøgle: IoT-enheder og operatører skal sende headeren X-API-Key
* Rate limiting: begrænser antal kald pr. klient (beskytter tilgængeligheden og mod brute force)
* Afviste kald logges som sikkerhedshændelser med klientens IP, men aldrig med selve nøglen
"""

from __future__ import annotations

import logging
import math
import secrets
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Annotated

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

logger = logging.getLogger("app.security")

api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="API-nøgle for IoT-enheder og operatører i kontrolcentret",
)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "ukendt"


def require_api_key(request: Request, api_key: Annotated[str | None, Security(api_key_header)]) -> None:
    expected: str = request.app.state.settings.api_key
    # compare_digest sammenligner i konstant tid og lækker derfor ikke nøglen via svartider
    if api_key is None or not secrets.compare_digest(api_key.encode(), expected.encode()):
        logger.warning(
            "Afvist kald: manglende eller ugyldig API-nøgle",
            extra={"client_ip": client_ip(request), "path": request.url.path, "status_code": 401},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Manglende eller ugyldig API-nøgle",
            headers={"WWW-Authenticate": "ApiKey"},
        )


class RateLimiter:
    """Glidende vindue: højst `limit` kald pr. klient inden for `window_seconds`.

    Tilstanden ligger i hukommelsen og gælder derfor pr. instans. Kører API'et i flere
    instanser, skal tællingen flyttes til fx Redis eller en API gateway.
    """

    def __init__(self, limit: int, window_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, client: str) -> int | None:
        """Registrerer et kald. Returnerer None hvis det er tilladt, ellers sekunder til næste forsøg."""
        now = self._clock()
        with self._lock:
            hits = self._hits[client]
            while hits and hits[0] <= now - self._window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1, math.ceil(hits[0] + self._window - now))
            hits.append(now)
            return None


def enforce_rate_limit(request: Request) -> None:
    limiter: RateLimiter | None = request.app.state.rate_limiter
    if limiter is None:
        return
    ip = client_ip(request)
    retry_after = limiter.check(ip)
    if retry_after is not None:
        logger.warning(
            "Rate limit overskredet",
            extra={"client_ip": ip, "path": request.url.path, "status_code": 429},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"For mange kald. Maks. {limiter.limit} pr. minut pr. klient",
            headers={"Retry-After": str(retry_after)},
        )
