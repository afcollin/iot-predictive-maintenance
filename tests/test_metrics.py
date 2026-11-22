"""Observability: /metrics udstiller drifts- og domænemålinger til Prometheus og Grafana."""

from __future__ import annotations

from prometheus_client import REGISTRY

from tests.conftest import reading


def sample(name: str, **labels: str) -> float:
    # Metrics er globale for processen, så testene måler ændringen i stedet for den absolutte værdi
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_metrics_er_slaaet_fra_som_standard(client):
    assert client.get("/metrics").status_code == 404


def test_anomali_og_kvittering_maales(make_client):
    client = make_client(metrics_enabled=True)
    received = sample("iot_sensor_values_received_total", metric="gearbox_vibration")
    critical = sample("iot_anomalies_detected_total", metric="gearbox_vibration", severity="CRITICAL")
    raised = sample("iot_alarm_actions_total", action="RAISED")
    detections = sample("iot_anomaly_detection_seconds_count")
    acknowledged = sample("iot_alarm_time_to_acknowledge_seconds_count", severity="CRITICAL")

    client.post("/api/v1/sensor-readings", json=reading(3.0))  # normal værdi
    alarm_id = client.post("/api/v1/sensor-readings", json=reading(12.5)).json()["alarm_id"]

    assert sample("iot_sensor_values_received_total", metric="gearbox_vibration") == received + 2
    assert sample("iot_anomalies_detected_total", metric="gearbox_vibration", severity="CRITICAL") == critical + 1
    assert sample("iot_alarm_actions_total", action="RAISED") == raised + 1
    assert sample("iot_anomaly_detection_seconds_count") == detections + 1

    body = client.get("/metrics").text
    assert 'iot_open_alarms{severity="CRITICAL"} 1.0' in body  # læst fra databasen ved hver scrape

    client.post(f"/api/v1/alarms/{alarm_id}/acknowledge", json={"acknowledged_by": "OP-117"}).raise_for_status()
    assert sample("iot_alarm_time_to_acknowledge_seconds_count", severity="CRITICAL") == acknowledged + 1
    assert 'iot_open_alarms{severity="CRITICAL"} 0.0' in client.get("/metrics").text


def test_afviste_maalinger_taelles_som_mulig_sensorfejl(make_client):
    client = make_client(metrics_enabled=True)
    before = sample("iot_sensor_values_rejected_total", reason="ImplausibleSensorValue")

    assert client.post("/api/v1/sensor-readings", json=reading(-5.0)).status_code == 422

    assert sample("iot_sensor_values_rejected_total", reason="ImplausibleSensorValue") == before + 1


def test_http_metrics_bruger_endpoint_skabelonen(make_client):
    client = make_client(metrics_enabled=True)
    route = "/api/v1/alarms/{alarm_id}"
    before = sample("http_requests_total", method="GET", route=route, status="404")

    client.get("/api/v1/alarms/findes-ikke")

    # Alarm-id'et indgår ikke i labels, ellers ville hver alarm skabe en ny tidsserie
    assert sample("http_requests_total", method="GET", route=route, status="404") == before + 1
    assert "findes-ikke" not in client.get("/metrics").text
