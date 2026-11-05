"""Notifikation af kontrolcentret (handlingen i event -> beslutning -> handling).

LogNotifier skriver altid alarmen i loggen. Er NOTIFY_WEBHOOK_URL sat, sendes alarmen også
til en webhook, fx en Teams-, Slack- eller Discord-kanal eller webhook.site under demo.
"""

from __future__ import annotations

import logging

import httpx

from app.domain import Alarm, AlarmAction

logger = logging.getLogger(__name__)


def format_message(alarm: Alarm, action: AlarmAction) -> str:
    headline = "NY ALARM" if action is AlarmAction.RAISED else "ALARM SKÆRPET"
    return (
        f"{headline} [{alarm.severity}] på {alarm.asset_id}: {alarm.metric} = {alarm.last_value} {alarm.unit} "
        f"(tærskel {alarm.threshold} {alarm.unit}, sensor {alarm.device_id})"
    )


class LogNotifier:
    def notify(self, alarm: Alarm, action: AlarmAction) -> None:
        logger.warning(
            format_message(alarm, action),
            extra={"alarm_id": alarm.id, "asset_id": alarm.asset_id, "severity": alarm.severity, "action": action},
        )


class WebhookNotifier:
    def __init__(self, url: str, timeout_seconds: float = 3.0) -> None:
        self._url = url
        self._timeout = timeout_seconds
        self._log = LogNotifier()

    def notify(self, alarm: Alarm, action: AlarmAction) -> None:
        self._log.notify(alarm, action)
        message = format_message(alarm, action)
        body = {
            "text": message,  # Slack / Teams
            "content": message,  # Discord
            "alarm": {
                "id": alarm.id,
                "action": action.value,
                "asset_id": alarm.asset_id,
                "device_id": alarm.device_id,
                "metric": alarm.metric,
                "severity": alarm.severity.value,
                "value": alarm.last_value,
                "threshold": alarm.threshold,
                "unit": alarm.unit,
            },
        }
        try:
            httpx.post(self._url, json=body, timeout=self._timeout).raise_for_status()
        except httpx.HTTPError as exc:
            # En fejlende notifikationskanal må ikke vælte modtagelsen af sensordata.
            # Alarmen er allerede gemt og kan ses via GET /api/v1/alarms.
            logger.error(
                f"Webhook-notifikation fejlede: {type(exc).__name__}",
                extra={"alarm_id": alarm.id, "asset_id": alarm.asset_id},
            )
