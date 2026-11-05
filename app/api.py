"""REST-endpoints (adapter). Oversætter HTTP til kald på application service og repository."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.domain import AlarmStatus, EventType, SensorReading, utcnow
from app.persistence import SqlMonitoringRepository
from app.schemas import (
    ID_PATTERN,
    AcknowledgeIn,
    AlarmOut,
    EventOut,
    ReadingResultOut,
    SensorReadingIn,
    ThresholdOut,
)
from app.security import enforce_rate_limit, require_api_key
from app.service import MonitoringService


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def get_repository(session: Annotated[Session, Depends(get_session)]) -> SqlMonitoringRepository:
    return SqlMonitoringRepository(session)


def get_service(
    request: Request, repository: Annotated[SqlMonitoringRepository, Depends(get_repository)]
) -> MonitoringService:
    return MonitoringService(repository, request.app.state.notifier, request.app.state.policy)


Service = Annotated[MonitoringService, Depends(get_service)]
Repository = Annotated[SqlMonitoringRepository, Depends(get_repository)]
Limit = Annotated[int, Query(ge=1, le=500, description="Maks. antal resultater")]
AssetFilter = Annotated[str | None, Query(pattern=ID_PATTERN, description="Filtrér på anlæg")]

# Rate limiting kører før nøgletjekket, så gæt på API-nøglen også bliver begrænset
router = APIRouter(prefix="/api/v1", dependencies=[Depends(enforce_rate_limit), Depends(require_api_key)])


@router.post(
    "/sensor-readings",
    response_model=ReadingResultOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Sensormålinger"],
    summary="Modtag en sensormåling fra en IoT-enhed",
    description=(
        "Gemmer eventet **SensorValueReceived** og udfører threshold-check. Overskrides en tærskel, "
        "gemmes **AnomalyDetected**, og der oprettes eller opdateres en alarm, som kontrolcentret notificeres om."
    ),
    responses={401: {"description": "Manglende eller ugyldig API-nøgle"}, 422: {"description": "Ugyldig måling"}},
)
def receive_sensor_reading(body: SensorReadingIn, service: Service) -> ReadingResultOut:
    measured_at = body.measured_at or utcnow()
    if measured_at.tzinfo is None:
        measured_at = measured_at.replace(tzinfo=UTC)
    reading = SensorReading(
        asset_id=body.asset_id,
        device_id=body.device_id,
        metric=body.metric,
        value=body.value,
        measured_at=measured_at,
    )
    outcome = service.handle_sensor_reading(reading)
    return ReadingResultOut.model_validate(outcome)


@router.get("/alarms", response_model=list[AlarmOut], tags=["Alarmer"], summary="List alarmer")
def list_alarms(
    repository: Repository,
    status: AlarmStatus | None = None,
    asset_id: AssetFilter = None,
    limit: Limit = 50,
) -> list[AlarmOut]:
    return [AlarmOut.model_validate(a) for a in repository.list_alarms(status, asset_id, limit)]


@router.get("/alarms/{alarm_id}", response_model=AlarmOut, tags=["Alarmer"], summary="Hent én alarm")
def get_alarm(alarm_id: str, repository: Repository) -> AlarmOut:
    return AlarmOut.model_validate(repository.get_alarm(alarm_id))


@router.post(
    "/alarms/{alarm_id}/acknowledge",
    response_model=AlarmOut,
    tags=["Alarmer"],
    summary="Kvittér for en alarm",
    description="En operatør i kontrolcentret tager ansvar for alarmen. Gemmer eventet **AlarmAcknowledged**.",
    responses={404: {"description": "Alarmen findes ikke"}, 409: {"description": "Alarmen er allerede kvitteret"}},
)
def acknowledge_alarm(alarm_id: str, body: AcknowledgeIn, service: Service) -> AlarmOut:
    return AlarmOut.model_validate(service.acknowledge_alarm(alarm_id, body.acknowledged_by))


@router.get("/events", response_model=list[EventOut], tags=["Domain events"], summary="List domain events")
def list_events(
    repository: Repository,
    event_type: EventType | None = None,
    asset_id: AssetFilter = None,
    limit: Limit = 50,
) -> list[EventOut]:
    return [EventOut.model_validate(e) for e in repository.list_events(event_type, asset_id, limit)]


@router.get("/thresholds", response_model=list[ThresholdOut], tags=["Tærskelregler"], summary="List tærskelregler")
def list_thresholds(request: Request) -> list[ThresholdOut]:
    return [ThresholdOut.model_validate(r) for r in request.app.state.policy.rules]
