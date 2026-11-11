"""Samtidighed: invarianten "højst én åben alarm pr. anlæg og målepunkt" skal holde med flere instanser."""

from __future__ import annotations

from app.persistence import SqlMonitoringRepository
from tests.conftest import reading


def test_concurrent_anomaly_does_not_create_a_second_open_alarm(client, monkeypatch):
    first = client.post("/api/v1/sensor-readings", json=reading(12.0)).json()
    assert first["alarm_action"] == "RAISED"

    # Simulér en anden instans, der læste databasen, før alarmen ovenfor var gemt:
    # første opslag ser "ingen åben alarm", de følgende opslag ser den rigtige tilstand.
    real_find = SqlMonitoringRepository.find_open_alarm
    calls = {"n": 0}

    def stale_then_real(self, asset_id, metric):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_find(self, asset_id, metric)

    monkeypatch.setattr(SqlMonitoringRepository, "find_open_alarm", stale_then_real)

    second = client.post("/api/v1/sensor-readings", json=reading(12.5))

    assert second.status_code == 201
    assert second.json()["alarm_action"] == "REPEATED"  # registreret på den eksisterende alarm
    assert second.json()["alarm_id"] == first["alarm_id"]
    alarms = client.get("/api/v1/alarms").json()
    assert len(alarms) == 1
    assert alarms[0]["occurrences"] == 2
    # Det afviste forsøg blev rullet helt tilbage: kun ét SensorValueReceived pr. måling
    received = client.get("/api/v1/events", params={"event_type": "SensorValueReceived"}).json()
    assert len(received) == 2
