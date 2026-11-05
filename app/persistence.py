"""Persistering (adapter): domain events gemmes append-only, alarmer gemmes som output.

Databasen kører som en separat container (PostgreSQL) og tilgås kun gennem dette modul.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import fields
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Engine, Float, Integer, String, create_engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.domain import Alarm, AlarmNotFound, AlarmStatus, DomainEvent, EventType, Severity

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class EventRecord(Base):
    """Event store: hvert domain event er én række, der aldrig opdateres eller slettes."""

    __tablename__ = "domain_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    asset_id: Mapped[str] = mapped_column(String(50), index=True)
    device_id: Mapped[str] = mapped_column(String(50))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    causation_id: Mapped[str | None] = mapped_column(String(36))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class AlarmRecord(Base):
    """Output: den aktuelle tilstand af hver alarm."""

    __tablename__ = "alarms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(50), index=True)
    device_id: Mapped[str] = mapped_column(String(50))
    metric: Mapped[str] = mapped_column(String(40))
    unit: Mapped[str] = mapped_column(String(10))
    severity: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(15), index=True)
    threshold: Mapped[float] = mapped_column(Float)
    first_value: Mapped[float] = mapped_column(Float)
    last_value: Mapped[float] = mapped_column(Float)
    occurrences: Mapped[int] = mapped_column(Integer)
    anomaly_event_id: Mapped[str] = mapped_column(String(36))
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(100))


_ALARM_FIELDS = [f.name for f in fields(Alarm)]


def _to_alarm(record: AlarmRecord) -> Alarm:
    data = {name: getattr(record, name) for name in _ALARM_FIELDS}
    data["severity"] = Severity(record.severity)
    data["status"] = AlarmStatus(record.status)
    return Alarm(**data)


def _to_event(record: EventRecord) -> DomainEvent:
    return DomainEvent(
        event_type=EventType(record.event_type),
        asset_id=record.asset_id,
        device_id=record.device_id,
        payload=record.payload,
        causation_id=record.causation_id,
        event_id=record.event_id,
        occurred_at=record.occurred_at,
    )


class SqlMonitoringRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_event(self, event: DomainEvent) -> None:
        self._session.add(
            EventRecord(
                event_id=event.event_id,
                event_type=event.event_type.value,
                asset_id=event.asset_id,
                device_id=event.device_id,
                occurred_at=event.occurred_at,
                causation_id=event.causation_id,
                payload=event.payload,
            )
        )

    def find_open_alarm(self, asset_id: str, metric: str) -> Alarm | None:
        record = self._session.scalars(
            select(AlarmRecord)
            .where(
                AlarmRecord.asset_id == asset_id,
                AlarmRecord.metric == metric,
                AlarmRecord.status == AlarmStatus.OPEN.value,
            )
            .order_by(AlarmRecord.raised_at.desc())
            .limit(1)
        ).first()
        return _to_alarm(record) if record else None

    def get_alarm(self, alarm_id: str) -> Alarm:
        record = self._session.get(AlarmRecord, alarm_id)
        if record is None:
            raise AlarmNotFound(f"Alarm {alarm_id} findes ikke")
        return _to_alarm(record)

    def save_alarm(self, alarm: Alarm) -> None:
        record = self._session.get(AlarmRecord, alarm.id) or AlarmRecord(id=alarm.id)
        for name in _ALARM_FIELDS:
            value = getattr(alarm, name)
            setattr(record, name, value.value if hasattr(value, "value") else value)
        self._session.add(record)

    def list_alarms(
        self, status: AlarmStatus | None = None, asset_id: str | None = None, limit: int = 50
    ) -> list[Alarm]:
        query = select(AlarmRecord).order_by(AlarmRecord.last_seen_at.desc()).limit(limit)
        if status is not None:
            query = query.where(AlarmRecord.status == status.value)
        if asset_id is not None:
            query = query.where(AlarmRecord.asset_id == asset_id)
        return [_to_alarm(r) for r in self._session.scalars(query)]

    def list_events(
        self, event_type: EventType | None = None, asset_id: str | None = None, limit: int = 50
    ) -> list[DomainEvent]:
        query = select(EventRecord).order_by(EventRecord.occurred_at.desc()).limit(limit)
        if event_type is not None:
            query = query.where(EventRecord.event_type == event_type.value)
        if asset_id is not None:
            query = query.where(EventRecord.asset_id == asset_id)
        return [_to_event(r) for r in self._session.scalars(query)]

    def commit(self) -> None:
        self._session.commit()


def create_db_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args=connect_args,
        json_serializer=lambda obj: json.dumps(obj, ensure_ascii=False),  # gem fx "°C" læsbart
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def init_db(engine: Engine, attempts: int = 10, delay_seconds: float = 2.0) -> None:
    """Opretter tabellerne. Prøver igen, hvis databasen endnu ikke er klar (fx ved opstart i Docker)."""
    for attempt in range(1, attempts + 1):
        try:
            Base.metadata.create_all(engine)
            return
        except OperationalError:
            if attempt == attempts:
                raise
            logger.warning(f"Database ikke klar (forsøg {attempt}/{attempts}), prøver igen om {delay_seconds}s")
            time.sleep(delay_seconds)


def database_is_healthy(session_factory: sessionmaker[Session]) -> bool:
    try:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
        return True
    except OperationalError:
        logger.exception("Health check: databasen svarer ikke")
        return False
