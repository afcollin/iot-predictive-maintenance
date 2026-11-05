"""API-kontrakt: request- og response-modeller. De bliver også til Swagger-dokumentationen."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain import AlarmAction, AlarmStatus, Direction, EventType, Severity

# Id'er begrænses til et sikkert tegnsæt (inputvalidering, beskytter bl.a. logs mod injection)
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,49}$"


class SensorReadingIn(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"asset_id": "WTG-AAL-07", "device_id": "SNS-000123", "metric": "gearbox_vibration", "value": 12.4}
            ]
        }
    )

    asset_id: str = Field(pattern=ID_PATTERN, description="Kundens anlæg, fx en vindmølle")
    device_id: str = Field(pattern=ID_PATTERN, description="IoT-sensoren, der har foretaget målingen")
    metric: str = Field(pattern=r"^[a-z_]{1,40}$", description="Målepunkt. Se GET /api/v1/thresholds")
    value: float = Field(allow_inf_nan=False, description="Den målte værdi i målepunktets enhed")
    measured_at: datetime | None = Field(
        default=None, description="Måletidspunkt (ISO 8601). Udelades det, bruges modtagelsestidspunktet"
    )


class ReadingResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    reading_event_id: str = Field(description="Id på SensorValueReceived-eventet")
    anomaly_detected: bool
    severity: Severity | None = None
    anomaly_event_id: str | None = Field(default=None, description="Id på AnomalyDetected-eventet")
    alarm_id: str | None = None
    alarm_action: AlarmAction | None = Field(
        default=None, description="RAISED = ny alarm, ESCALATED = skærpet alarm, REPEATED = gentagelse"
    )


class AlarmOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    asset_id: str
    device_id: str
    metric: str
    unit: str
    severity: Severity
    status: AlarmStatus
    threshold: float
    first_value: float
    last_value: float
    occurrences: int
    anomaly_event_id: str
    raised_at: datetime
    last_seen_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by: str | None


class AcknowledgeIn(BaseModel):
    # Privacy by design (GDPR art. 5, dataminimering): vi gemmer et medarbejder-id, ikke et navn
    acknowledged_by: str = Field(
        pattern=ID_PATTERN,
        examples=["OP-117"],
        description="Operatørens medarbejder-id. Navne gemmes ikke (dataminimering)",
    )


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: str
    event_type: EventType
    asset_id: str
    device_id: str
    occurred_at: datetime
    causation_id: str | None
    payload: dict[str, Any]


class ThresholdOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    metric: str
    unit: str
    description: str
    warning: float
    critical: float
    direction: Direction
    plausible_min: float
    plausible_max: float


class HealthOut(BaseModel):
    status: str
    database: str
    version: str
