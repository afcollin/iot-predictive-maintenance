"""Domænemodel for bounded context'en Asset Condition Monitoring (core subdomain).

Modulet indeholder det fælles sprog fra Event Storming i delopgave 1: sensormålinger,
tærskelregler, domain events og alarmer. Koden er ren Python uden afhængighed af
web-framework eller database, så forretningsreglerne kan testes isoleret
(ports & adapters, jf. Khononov, 2021, kap. 8).

Ubiquitous language:
    Asset       kundens anlæg, fx en vindmølle (WTG-AAL-07)
    IoT Device  sensoren monteret på anlægget (SNS-000123)
    Metric      det målepunkt, sensoren måler, fx vibration i gearkassen
    Anomaly     en måling, der overskrider en tærskel
    Alarm       den handling, kontrolcentret skal reagere på
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


# --- Brud på forretningsregler ------------------------------------------------


class DomainError(Exception):
    """Basisklasse for brud på forretningsregler."""


class UnknownMetric(DomainError):
    """Målepunktet findes ikke i tærskelpolitikken."""


class ImplausibleSensorValue(DomainError):
    """Værdien kan ikke være fysisk korrekt. Det er en sensorfejl, ikke en anomali på anlægget."""


class AlarmNotFound(DomainError):
    """Der findes ingen alarm med det angivne id."""


class AlarmAlreadyAcknowledged(DomainError):
    """En alarm kan kun kvitteres én gang."""


# --- Value objects ------------------------------------------------------------


class Severity(StrEnum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return 2 if self is Severity.CRITICAL else 1


class Direction(StrEnum):
    ABOVE = "ABOVE"  # anomali når værdien er for høj, fx vibration og temperatur
    BELOW = "BELOW"  # anomali når værdien er for lav, fx olietryk


@dataclass(frozen=True)
class SensorReading:
    """En måling fra en IoT Device monteret på et Asset."""

    asset_id: str
    device_id: str
    metric: str
    value: float
    measured_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class ThresholdRule:
    """Forretningsregel: hvornår er en måling en anomali, og hvor alvorlig er den?"""

    metric: str
    unit: str
    description: str
    warning: float
    critical: float
    direction: Direction
    plausible_min: float
    plausible_max: float

    def is_plausible(self, value: float) -> bool:
        return math.isfinite(value) and self.plausible_min <= value <= self.plausible_max

    def classify(self, value: float) -> Severity | None:
        """Threshold-check. Returnerer alvorligheden, eller None hvis målingen er normal."""
        if self.direction is Direction.ABOVE:
            if value >= self.critical:
                return Severity.CRITICAL
            if value >= self.warning:
                return Severity.WARNING
        else:
            if value <= self.critical:
                return Severity.CRITICAL
            if value <= self.warning:
                return Severity.WARNING
        return None

    def threshold_for(self, severity: Severity) -> float:
        return self.critical if severity is Severity.CRITICAL else self.warning


# Eksempelværdier for en vindmølle. I drift ville de være konfigureret pr. anlægstype.
DEFAULT_RULES: tuple[ThresholdRule, ...] = (
    ThresholdRule(
        metric="gearbox_vibration",
        unit="mm/s",
        description="Vibration (RMS) i gearkassen",
        warning=7.1,
        critical=11.0,
        direction=Direction.ABOVE,
        plausible_min=0.0,
        plausible_max=100.0,
    ),
    ThresholdRule(
        metric="bearing_temperature",
        unit="°C",
        description="Temperatur i hovedlejet",
        warning=80.0,
        critical=95.0,
        direction=Direction.ABOVE,
        plausible_min=-40.0,
        plausible_max=150.0,
    ),
    ThresholdRule(
        metric="oil_pressure",
        unit="bar",
        description="Olietryk i smøresystemet",
        warning=1.5,
        critical=1.0,
        direction=Direction.BELOW,
        plausible_min=0.0,
        plausible_max=10.0,
    ),
)


class ThresholdPolicy:
    """Samlingen af tærskelregler, som en måling vurderes efter."""

    def __init__(self, rules: tuple[ThresholdRule, ...] = DEFAULT_RULES) -> None:
        self._rules = {rule.metric: rule for rule in rules}

    @property
    def rules(self) -> list[ThresholdRule]:
        return list(self._rules.values())

    def rule_for(self, reading: SensorReading) -> ThresholdRule:
        """Validerer målingen og returnerer den regel, den skal vurderes efter."""
        rule = self._rules.get(reading.metric)
        if rule is None:
            known = ", ".join(sorted(self._rules))
            raise UnknownMetric(f"Ukendt målepunkt '{reading.metric}'. Kendte målepunkter: {known}")
        if not rule.is_plausible(reading.value):
            raise ImplausibleSensorValue(
                f"{reading.value} {rule.unit} er uden for det fysisk mulige interval "
                f"[{rule.plausible_min}; {rule.plausible_max}] for {reading.metric}. "
                f"Målingen afvises som mulig sensorfejl."
            )
        return rule


# --- Domain events ------------------------------------------------------------


class EventType(StrEnum):
    SENSOR_VALUE_RECEIVED = "SensorValueReceived"
    ANOMALY_DETECTED = "AnomalyDetected"
    ALARM_ACKNOWLEDGED = "AlarmAcknowledged"


@dataclass(frozen=True)
class DomainEvent:
    """Noget, der er sket i domænet (navngivet i datid). Gemmes append-only."""

    event_type: EventType
    asset_id: str
    device_id: str
    payload: dict[str, Any]
    causation_id: str | None = None  # id på det event, der udløste dette event
    event_id: str = field(default_factory=new_id)
    occurred_at: datetime = field(default_factory=utcnow)


def sensor_value_received(reading: SensorReading, rule: ThresholdRule) -> DomainEvent:
    return DomainEvent(
        event_type=EventType.SENSOR_VALUE_RECEIVED,
        asset_id=reading.asset_id,
        device_id=reading.device_id,
        payload={
            "metric": reading.metric,
            "value": reading.value,
            "unit": rule.unit,
            "measured_at": reading.measured_at.isoformat(),
        },
    )


def anomaly_detected(
    reading: SensorReading, rule: ThresholdRule, severity: Severity, causation_id: str
) -> DomainEvent:
    return DomainEvent(
        event_type=EventType.ANOMALY_DETECTED,
        asset_id=reading.asset_id,
        device_id=reading.device_id,
        causation_id=causation_id,
        payload={
            "metric": reading.metric,
            "value": reading.value,
            "unit": rule.unit,
            "severity": severity.value,
            "threshold": rule.threshold_for(severity),
            "direction": rule.direction.value,
        },
    )


# --- Aggregate: Alarm ---------------------------------------------------------


class AlarmStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"


class AlarmAction(StrEnum):
    RAISED = "RAISED"  # ny alarm oprettet, kontrolcentret notificeres
    ESCALATED = "ESCALATED"  # åben alarm skærpet fra WARNING til CRITICAL, notificeres igen
    REPEATED = "REPEATED"  # anomalien gentager sig på en åben alarm, ingen ny notifikation


@dataclass
class Alarm:
    """Aggregate med reglen: højst én åben alarm pr. anlæg og målepunkt.

    Reglen forhindrer alarmstorme i kontrolcentret, når en sensor bliver ved med at
    sende værdier over tærsklen.
    """

    asset_id: str
    device_id: str
    metric: str
    unit: str
    severity: Severity
    threshold: float
    first_value: float
    last_value: float
    anomaly_event_id: str
    raised_at: datetime
    last_seen_at: datetime
    id: str = field(default_factory=new_id)
    status: AlarmStatus = AlarmStatus.OPEN
    occurrences: int = 1
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None

    @classmethod
    def raise_for(cls, anomaly: DomainEvent) -> Alarm:
        p = anomaly.payload
        return cls(
            asset_id=anomaly.asset_id,
            device_id=anomaly.device_id,
            metric=p["metric"],
            unit=p["unit"],
            severity=Severity(p["severity"]),
            threshold=p["threshold"],
            first_value=p["value"],
            last_value=p["value"],
            anomaly_event_id=anomaly.event_id,
            raised_at=anomaly.occurred_at,
            last_seen_at=anomaly.occurred_at,
        )

    def register_anomaly(self, anomaly: DomainEvent) -> AlarmAction:
        """Registrerer en ny anomali på samme anlæg og målepunkt, mens alarmen er åben."""
        if self.status is not AlarmStatus.OPEN:
            raise DomainError("Kun åbne alarmer kan registrere nye anomalier")
        severity = Severity(anomaly.payload["severity"])
        self.occurrences += 1
        self.last_value = anomaly.payload["value"]
        self.last_seen_at = anomaly.occurred_at
        if severity.rank > self.severity.rank:
            self.severity = severity
            self.threshold = anomaly.payload["threshold"]
            return AlarmAction.ESCALATED
        return AlarmAction.REPEATED

    def acknowledge(self, by: str) -> DomainEvent:
        """En operatør i kontrolcentret kvitterer for alarmen og tager ansvar for opfølgning."""
        if self.status is AlarmStatus.ACKNOWLEDGED:
            raise AlarmAlreadyAcknowledged(f"Alarm {self.id} er allerede kvitteret af {self.acknowledged_by}")
        self.status = AlarmStatus.ACKNOWLEDGED
        self.acknowledged_at = utcnow()
        self.acknowledged_by = by
        return DomainEvent(
            event_type=EventType.ALARM_ACKNOWLEDGED,
            asset_id=self.asset_id,
            device_id=self.device_id,
            causation_id=self.anomaly_event_id,
            payload={"alarm_id": self.id, "metric": self.metric, "acknowledged_by": by},
        )
