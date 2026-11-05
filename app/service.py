"""Application service: event -> beslutning -> handling.

Servicen orkestrerer forløbet fra en sensormåling modtages, til en alarm er gemt og
kontrolcentret er notificeret. Selve forretningsreglerne ligger i domain.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from app.domain import (
    Alarm,
    AlarmAction,
    DomainEvent,
    SensorReading,
    Severity,
    ThresholdPolicy,
    anomaly_detected,
    sensor_value_received,
)

logger = logging.getLogger(__name__)


class MonitoringRepository(Protocol):
    """Port til persistering. Implementeres af SqlMonitoringRepository."""

    def add_event(self, event: DomainEvent) -> None: ...
    def find_open_alarm(self, asset_id: str, metric: str) -> Alarm | None: ...
    def get_alarm(self, alarm_id: str) -> Alarm: ...
    def save_alarm(self, alarm: Alarm) -> None: ...
    def commit(self) -> None: ...


class Notifier(Protocol):
    """Port til notifikation af kontrolcentret."""

    def notify(self, alarm: Alarm, action: AlarmAction) -> None: ...


@dataclass(frozen=True)
class ReadingOutcome:
    reading_event_id: str
    anomaly_detected: bool
    severity: Severity | None = None
    anomaly_event_id: str | None = None
    alarm_id: str | None = None
    alarm_action: AlarmAction | None = None


class MonitoringService:
    def __init__(self, repository: MonitoringRepository, notifier: Notifier, policy: ThresholdPolicy) -> None:
        self._repo = repository
        self._notifier = notifier
        self._policy = policy

    def handle_sensor_reading(self, reading: SensorReading) -> ReadingOutcome:
        # 1. Validering: kendt målepunkt og fysisk mulig værdi (ellers DomainError)
        rule = self._policy.rule_for(reading)

        # 2. Event: Sensor Value Received
        received = sensor_value_received(reading, rule)
        self._repo.add_event(received)

        # 3. Beslutning: threshold-check
        severity = rule.classify(reading.value)
        if severity is None:
            self._repo.commit()
            logger.info(
                "Måling inden for normalområdet",
                extra={"event_type": received.event_type, "asset_id": reading.asset_id, "metric": reading.metric},
            )
            return ReadingOutcome(reading_event_id=received.event_id, anomaly_detected=False)

        # 4. Event: Anomaly Detected
        anomaly = anomaly_detected(reading, rule, severity, causation_id=received.event_id)
        self._repo.add_event(anomaly)

        # 5. Handling: opret ny alarm eller opdatér den åbne alarm for samme anlæg og målepunkt
        alarm = self._repo.find_open_alarm(reading.asset_id, reading.metric)
        if alarm is None:
            alarm = Alarm.raise_for(anomaly)
            action = AlarmAction.RAISED
        else:
            action = alarm.register_anomaly(anomaly)
        self._repo.save_alarm(alarm)
        self._repo.commit()

        logger.warning(
            "Anomali registreret",
            extra={
                "event_type": anomaly.event_type,
                "asset_id": reading.asset_id,
                "metric": reading.metric,
                "severity": severity,
                "alarm_id": alarm.id,
                "action": action,
            },
        )

        # 6. Notifikation sker først efter commit, så der kun notificeres om gemte alarmer
        if action in (AlarmAction.RAISED, AlarmAction.ESCALATED):
            self._notifier.notify(alarm, action)

        return ReadingOutcome(
            reading_event_id=received.event_id,
            anomaly_detected=True,
            severity=severity,
            anomaly_event_id=anomaly.event_id,
            alarm_id=alarm.id,
            alarm_action=action,
        )

    def acknowledge_alarm(self, alarm_id: str, acknowledged_by: str) -> Alarm:
        alarm = self._repo.get_alarm(alarm_id)
        event = alarm.acknowledge(acknowledged_by)
        self._repo.save_alarm(alarm)
        self._repo.add_event(event)
        self._repo.commit()
        logger.info(
            "Alarm kvitteret",
            extra={"event_type": event.event_type, "asset_id": alarm.asset_id, "alarm_id": alarm.id},
        )
        return alarm
