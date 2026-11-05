from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain import Alarm, AlarmAction
from app.main import create_app

API_KEY = "test-key-123"


class RecordingNotifier:
    """Test-dobbelt, der husker notifikationer i stedet for at sende dem."""

    def __init__(self) -> None:
        self.sent: list[tuple[Alarm, AlarmAction]] = []

    def notify(self, alarm: Alarm, action: AlarmAction) -> None:
        self.sent.append((alarm, action))


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest.fixture
def make_client(tmp_path, notifier: RecordingNotifier) -> Iterator[Callable[..., TestClient]]:
    """Fabrik til testklienter, så den enkelte test kan ændre indstillinger (fx rate limit)."""
    opened: list[TestClient] = []

    def _make(raise_server_exceptions: bool = True, **overrides) -> TestClient:
        settings = Settings(
            _env_file=None,
            database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
            api_key=API_KEY,
            log_level="WARNING",
            **overrides,
        )
        app = create_app(settings=settings, notifier=notifier)
        test_client = TestClient(app, raise_server_exceptions=raise_server_exceptions)
        test_client.__enter__()  # kører app'ens lifespan (opretter tabeller)
        test_client.headers.update({"X-API-Key": API_KEY})
        opened.append(test_client)
        return test_client

    yield _make
    for test_client in opened:
        test_client.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def reading(value: float, metric: str = "gearbox_vibration", asset_id: str = "WTG-AAL-07") -> dict:
    return {"asset_id": asset_id, "device_id": "SNS-000123", "metric": metric, "value": value}
