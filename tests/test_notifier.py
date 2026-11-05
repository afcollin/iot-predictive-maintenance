"""Notifikation: en fejlende webhook må ikke vælte modtagelsen af sensordata."""

from __future__ import annotations

import httpx

from app.domain import Alarm, AlarmAction, SensorReading, ThresholdPolicy, anomaly_detected
from app.notifier import WebhookNotifier, format_message


def _alarm() -> Alarm:
    reading = SensorReading("WTG-AAL-07", "SNS-000123", "gearbox_vibration", 12.4)
    rule = ThresholdPolicy().rule_for(reading)
    return Alarm.raise_for(anomaly_detected(reading, rule, rule.classify(12.4), causation_id="evt-1"))


def test_message_is_readable_for_the_control_center():
    message = format_message(_alarm(), AlarmAction.RAISED)
    assert message.startswith("NY ALARM [CRITICAL] på WTG-AAL-07")
    assert "12.4 mm/s" in message


def test_webhook_posts_alarm(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    WebhookNotifier("https://example.test/hook").notify(_alarm(), AlarmAction.RAISED)

    url, body = calls[0]
    assert url == "https://example.test/hook"
    assert body["alarm"]["severity"] == "CRITICAL"


def test_webhook_failure_is_logged_not_raised(monkeypatch, caplog):
    def failing_post(url, json, timeout):
        raise httpx.ConnectError("forbindelse afvist")

    monkeypatch.setattr(httpx, "post", failing_post)
    WebhookNotifier("https://example.test/hook").notify(_alarm(), AlarmAction.RAISED)

    assert "Webhook-notifikation fejlede" in caplog.text
