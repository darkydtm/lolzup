import pytest
from pydantic import ValidationError

from lolzup.config import Settings


@pytest.mark.unit
def test_settings_require_positive_owner_id(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("BOT_TOKEN", "123:test")
	monkeypatch.setenv("OWNER_ID", "0")
	monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://bot:bot@db/bot")

	with pytest.raises(ValidationError):
		Settings()  # type: ignore[call-arg]
