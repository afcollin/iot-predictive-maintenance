"""Sikkerhed: rate limiting, logning af afviste kald og sikker fejlhåndtering."""

from __future__ import annotations

from app.security import RateLimiter
from app.service import MonitoringService
from tests.conftest import reading


def test_rate_limiter_uses_sliding_window_per_client():
    now = [0.0]
    limiter = RateLimiter(limit=2, window_seconds=60, clock=lambda: now[0])

    assert limiter.check("10.0.0.1") is None
    assert limiter.check("10.0.0.1") is None
    assert limiter.check("10.0.0.1") == 60  # tredje kald afvises, prøv igen om 60 s
    assert limiter.check("10.0.0.2") is None  # en anden klient har sin egen kvote

    now[0] = 60.0
    assert limiter.check("10.0.0.1") is None  # vinduet er gledet videre


def test_api_returns_429_when_rate_limit_is_exceeded(make_client):
    client = make_client(rate_limit_per_minute=3)

    statuses = [client.get("/api/v1/thresholds").status_code for _ in range(4)]

    assert statuses == [200, 200, 200, 429]
    assert "Retry-After" in client.get("/api/v1/thresholds").headers


def test_rate_limit_also_applies_to_wrong_api_keys(make_client):
    client = make_client(rate_limit_per_minute=2)
    wrong = {"X-API-Key": "gaet-paa-noeglen"}

    statuses = [client.get("/api/v1/alarms", headers=wrong).status_code for _ in range(3)]

    assert statuses == [401, 401, 429]  # brute force af nøglen bremses


def test_health_and_docs_are_not_rate_limited(make_client):
    client = make_client(rate_limit_per_minute=1)
    assert all(client.get("/health").status_code == 200 for _ in range(3))


def test_rejected_api_key_is_logged_as_security_event_without_the_key(client, caplog):
    client.post("/api/v1/sensor-readings", json=reading(5.0), headers={"X-API-Key": "forkert-noegle-xyz"})

    record = next(r for r in caplog.records if "ugyldig API-nøgle" in r.getMessage())
    assert record.levelname == "WARNING"
    assert record.client_ip == "testclient"
    assert "forkert-noegle-xyz" not in caplog.text


def test_unexpected_error_fails_securely(make_client, monkeypatch, caplog):
    def explode(self, reading):
        raise RuntimeError("intern detalje: forbindelsesstreng postgresql://iot:hemmelig@db")

    monkeypatch.setattr(MonitoringService, "handle_sensor_reading", explode)
    client = make_client(raise_server_exceptions=False)

    response = client.post("/api/v1/sensor-readings", json=reading(5.0))

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "InternalServerError"
    assert "hemmelig" not in response.text  # klienten får ingen interne detaljer
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert "hemmelig" in caplog.text  # men detaljerne er i loggen til driften


def test_security_headers_found_by_dast_are_set(client):
    for path in ("/health", "/api/v1/thresholds"):
        headers = client.get(path).headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Cross-Origin-Resource-Policy"] == "same-origin"
        assert headers["X-Frame-Options"] == "DENY"
    assert client.get("/api/v1/thresholds").headers["Cache-Control"] == "no-store"


def test_successful_health_probes_are_not_written_to_the_audit_log(client, caplog):
    caplog.set_level("INFO")
    client.get("/health")
    client.get("/api/v1/thresholds")

    logged_paths = [getattr(r, "path", None) for r in caplog.records if r.getMessage() == "HTTP-request"]
    assert "/api/v1/thresholds" in logged_paths
    assert "/health" not in logged_paths


def test_acknowledge_requires_employee_id_not_a_name(client):
    alarm_id = client.post("/api/v1/sensor-readings", json=reading(12.0)).json()["alarm_id"]

    response = client.post(f"/api/v1/alarms/{alarm_id}/acknowledge", json={"acknowledged_by": "Jens Hansen"})

    assert response.status_code == 422
