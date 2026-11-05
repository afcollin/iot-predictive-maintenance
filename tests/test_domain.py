"""Unit tests af forretningsreglerne. Kører uden database og web-framework."""

from __future__ import annotations

import pytest

from app.domain import (
    Alarm,
    AlarmAction,
    AlarmAlreadyAcknowledged,
    AlarmStatus,
    ImplausibleSensorValue,
    SensorReading,
    Severity,
    ThresholdPolicy,
    UnknownMetric,
    anomaly_detected,
)

policy = ThresholdPolicy()


def rule(metric: str):
    return policy.rule_for(SensorReading("WTG-1", "SNS-1", metric, 1.0 if metric == "oil_pressure" else 5.0))


@pytest.mark.parametrize(
    ("value", "expected"),
    [(3.0, None), (7.0, None), (7.1, Severity.WARNING), (10.9, Severity.WARNING), (11.0, Severity.CRITICAL)],
)
def test_vibration_threshold_is_upper_bound(value, expected):
    assert rule("gearbox_vibration").classify(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(3.0, None), (1.6, None), (1.5, Severity.WARNING), (1.1, Severity.WARNING), (1.0, Severity.CRITICAL)],
)
def test_oil_pressure_threshold_is_lower_bound(value, expected):
    assert rule("oil_pressure").classify(value) == expected


def test_unknown_metric_is_rejected():
    with pytest.raises(UnknownMetric):
        policy.rule_for(SensorReading("WTG-1", "SNS-1", "wind_speed", 12.0))


@pytest.mark.parametrize("value", [-1.0, 250.0, float("nan"), float("inf")])
def test_physically_impossible_value_is_a_sensor_fault(value):
    with pytest.raises(ImplausibleSensorValue):
        policy.rule_for(SensorReading("WTG-1", "SNS-1", "gearbox_vibration", value))


def _anomaly(value: float):
    reading = SensorReading("WTG-1", "SNS-1", "gearbox_vibration", value)
    r = policy.rule_for(reading)
    return anomaly_detected(reading, r, r.classify(value), causation_id="evt-1")


def test_repeated_anomaly_updates_open_alarm_instead_of_raising_new():
    alarm = Alarm.raise_for(_anomaly(8.0))
    assert alarm.register_anomaly(_anomaly(9.0)) is AlarmAction.REPEATED
    assert alarm.occurrences == 2
    assert alarm.last_value == 9.0
    assert alarm.severity is Severity.WARNING


def test_alarm_escalates_from_warning_to_critical():
    alarm = Alarm.raise_for(_anomaly(8.0))
    assert alarm.register_anomaly(_anomaly(12.0)) is AlarmAction.ESCALATED
    assert alarm.severity is Severity.CRITICAL
    assert alarm.threshold == 11.0


def test_critical_alarm_is_not_downgraded_by_a_warning():
    alarm = Alarm.raise_for(_anomaly(12.0))
    assert alarm.register_anomaly(_anomaly(8.0)) is AlarmAction.REPEATED
    assert alarm.severity is Severity.CRITICAL


def test_alarm_can_only_be_acknowledged_once():
    alarm = Alarm.raise_for(_anomaly(12.0))
    event = alarm.acknowledge("OP-117")
    assert alarm.status is AlarmStatus.ACKNOWLEDGED
    assert event.payload["alarm_id"] == alarm.id
    with pytest.raises(AlarmAlreadyAcknowledged):
        alarm.acknowledge("OP-204")
