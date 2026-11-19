"""Metrics til Prometheus og Grafana (observability).

Endpointet /metrics udstiller to slags målinger:
- Drift (RED-metoden): antal kald, fejl og svartider pr. endpoint
- Domænet: sensorværdier, anomalier, alarmer og tiden fra måling til detektion og fra alarm til kvittering

Counter tæller kun op (fx antal anomalier). Gauge kan gå op og ned (fx antal åbne alarmer lige nu).
Histogram fordeler værdier i spande, så Grafana kan beregne gennemsnit og p95.
"""

from __future__ import annotations

from datetime import UTC, datetime

from prometheus_client import Counter, Gauge, Histogram

from app.domain import Alarm, Severity, utcnow
from app.service import ReadingOutcome

# --- Drift ---
HTTP_REQUESTS = Counter("http_requests_total", "HTTP-kald pr. endpoint og statuskode", ["method", "route", "status"])
HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "Svartid pr. endpoint",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# --- Domænet (Asset Condition Monitoring) ---
SENSOR_VALUES_RECEIVED = Counter(
    "iot_sensor_values_received_total", "Gyldige sensormålinger (SensorValueReceived)", ["metric"]
)
SENSOR_VALUES_REJECTED = Counter(
    "iot_sensor_values_rejected_total", "Afviste målinger: ukendt målepunkt eller fysisk umulig værdi", ["reason"]
)
ANOMALIES_DETECTED = Counter(
    "iot_anomalies_detected_total", "Anomalier (AnomalyDetected) pr. målepunkt og alvorsgrad", ["metric", "severity"]
)
ALARM_ACTIONS = Counter("iot_alarm_actions_total", "Alarmhandlinger: RAISED, ESCALATED eller REPEATED", ["action"])
OPEN_ALARMS = Gauge("iot_open_alarms", "Åbne alarmer lige nu pr. alvorsgrad", ["severity"])
DETECTION_SECONDS = Histogram(
    "iot_anomaly_detection_seconds",
    "Tid fra sensoren målte til anomalien blev detekteret (MTTD i domænet)",
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 300),
)
ACKNOWLEDGE_SECONDS = Histogram(
    "iot_alarm_time_to_acknowledge_seconds",
    "Tid fra alarmen blev rejst til en operatør kvitterede (MTTA)",
    ["severity"],
    buckets=(10, 30, 60, 300, 600, 900, 1800, 3600, 4 * 3600),
)


def _seconds_between(start: datetime, end: datetime) -> float:
    # SQLite gemmer tidspunkter uden tidszone; alle tidspunkter i løsningen er UTC.
    start, end = (t if t.tzinfo else t.replace(tzinfo=UTC) for t in (start, end))
    # max(0, ...): en sensor med forkert ur kan sende et tidspunkt i fremtiden
    return max(0.0, (end - start).total_seconds())


def record_reading(outcome: ReadingOutcome, metric: str, measured_at: datetime) -> None:
    SENSOR_VALUES_RECEIVED.labels(metric=metric).inc()
    if not outcome.anomaly_detected:
        return
    ANOMALIES_DETECTED.labels(metric=metric, severity=outcome.severity).inc()
    ALARM_ACTIONS.labels(action=outcome.alarm_action).inc()
    DETECTION_SECONDS.observe(_seconds_between(measured_at, utcnow()))


def record_acknowledged(alarm: Alarm) -> None:
    if alarm.acknowledged_at is not None:
        ACKNOWLEDGE_SECONDS.labels(severity=alarm.severity).observe(
            _seconds_between(alarm.raised_at, alarm.acknowledged_at)
        )


def set_open_alarms(counts: dict[str, int]) -> None:
    for severity in Severity:
        OPEN_ALARMS.labels(severity=severity.value).set(counts.get(severity.value, 0))
