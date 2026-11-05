"""Integrationstests: hele flowet gennem REST API'et til databasen."""

from __future__ import annotations

from tests.conftest import reading


def test_health_reports_database_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


def test_missing_or_wrong_api_key_is_rejected(client):
    assert client.post("/api/v1/sensor-readings", json=reading(5.0), headers={"X-API-Key": ""}).status_code == 401
    assert client.get("/api/v1/alarms", headers={"X-API-Key": "forkert-noegle"}).status_code == 401


def test_normal_reading_is_stored_as_event_without_alarm(client, notifier):
    response = client.post("/api/v1/sensor-readings", json=reading(3.2))

    assert response.status_code == 201
    body = response.json()
    assert body["anomaly_detected"] is False
    assert body["alarm_id"] is None

    events = client.get("/api/v1/events").json()
    assert [e["event_type"] for e in events] == ["SensorValueReceived"]
    assert events[0]["payload"]["value"] == 3.2
    assert client.get("/api/v1/alarms").json() == []
    assert notifier.sent == []


def test_anomaly_flow_event_decision_action(client, notifier):
    response = client.post("/api/v1/sensor-readings", json=reading(12.4))

    assert response.status_code == 201
    body = response.json()
    assert body["anomaly_detected"] is True
    assert body["severity"] == "CRITICAL"
    assert body["alarm_action"] == "RAISED"

    # Begge domain events er gemt, og AnomalyDetected peger på målingen, der udløste den
    events = {e["event_type"]: e for e in client.get("/api/v1/events").json()}
    assert events["AnomalyDetected"]["causation_id"] == events["SensorValueReceived"]["event_id"]
    assert events["AnomalyDetected"]["payload"]["threshold"] == 11.0

    # Output: alarmen er gemt og kontrolcentret er notificeret
    alarm = client.get(f"/api/v1/alarms/{body['alarm_id']}").json()
    assert alarm["status"] == "OPEN"
    assert alarm["asset_id"] == "WTG-AAL-07"
    assert len(notifier.sent) == 1


def test_repeated_anomalies_do_not_flood_the_control_center(client, notifier):
    first = client.post("/api/v1/sensor-readings", json=reading(8.0)).json()
    second = client.post("/api/v1/sensor-readings", json=reading(9.0)).json()

    assert second["alarm_action"] == "REPEATED"
    assert second["alarm_id"] == first["alarm_id"]
    alarms = client.get("/api/v1/alarms").json()
    assert len(alarms) == 1
    assert alarms[0]["occurrences"] == 2
    assert len(notifier.sent) == 1


def test_escalation_notifies_again(client, notifier):
    client.post("/api/v1/sensor-readings", json=reading(8.0))
    escalated = client.post("/api/v1/sensor-readings", json=reading(11.5)).json()

    assert escalated["alarm_action"] == "ESCALATED"
    assert [action for _, action in notifier.sent] == ["RAISED", "ESCALATED"]


def test_alarms_are_separate_per_asset_and_metric(client):
    client.post("/api/v1/sensor-readings", json=reading(12.0, asset_id="WTG-AAL-07"))
    client.post("/api/v1/sensor-readings", json=reading(12.0, asset_id="WTG-AAL-08"))
    client.post("/api/v1/sensor-readings", json=reading(0.8, metric="oil_pressure", asset_id="WTG-AAL-07"))

    assert len(client.get("/api/v1/alarms").json()) == 3
    assert len(client.get("/api/v1/alarms", params={"asset_id": "WTG-AAL-07"}).json()) == 2


def test_implausible_value_is_rejected_and_not_persisted(client):
    response = client.post("/api/v1/sensor-readings", json=reading(500.0))

    assert response.status_code == 422
    assert response.json()["error"] == "ImplausibleSensorValue"
    assert client.get("/api/v1/events").json() == []


def test_unknown_metric_and_malformed_input_are_rejected(client):
    assert client.post("/api/v1/sensor-readings", json=reading(5.0, metric="wind_speed")).status_code == 422
    bad_id = {**reading(5.0), "asset_id": "WTG; DROP TABLE alarms"}
    assert client.post("/api/v1/sensor-readings", json=bad_id).status_code == 422
    assert client.post("/api/v1/sensor-readings", json={"asset_id": "WTG-1"}).status_code == 422


def test_acknowledge_alarm(client):
    alarm_id = client.post("/api/v1/sensor-readings", json=reading(12.0)).json()["alarm_id"]

    response = client.post(f"/api/v1/alarms/{alarm_id}/acknowledge", json={"acknowledged_by": "OP-117"})
    assert response.status_code == 200
    assert response.json()["status"] == "ACKNOWLEDGED"

    again = client.post(f"/api/v1/alarms/{alarm_id}/acknowledge", json={"acknowledged_by": "OP-117"})
    assert again.status_code == 409

    acknowledged = client.get("/api/v1/events", params={"event_type": "AlarmAcknowledged"}).json()
    assert acknowledged[0]["payload"]["alarm_id"] == alarm_id


def test_new_anomaly_after_acknowledge_raises_new_alarm(client):
    first = client.post("/api/v1/sensor-readings", json=reading(12.0)).json()["alarm_id"]
    client.post(f"/api/v1/alarms/{first}/acknowledge", json={"acknowledged_by": "OP-117"})

    second = client.post("/api/v1/sensor-readings", json=reading(12.0)).json()
    assert second["alarm_action"] == "RAISED"
    assert second["alarm_id"] != first


def test_unknown_alarm_returns_404(client):
    assert client.get("/api/v1/alarms/findes-ikke").status_code == 404
    response = client.post("/api/v1/alarms/findes-ikke/acknowledge", json={"acknowledged_by": "OP-117"})
    assert response.status_code == 404


def test_thresholds_are_exposed(client):
    metrics = {t["metric"] for t in client.get("/api/v1/thresholds").json()}
    assert metrics == {"gearbox_vibration", "bearing_temperature", "oil_pressure"}
