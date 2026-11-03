import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from citypulse.settings import OFFICIAL_GBFS_DISCOVERY_URL, Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"
OTHER_URL = "https://example.org/gbfs/gbfs.json"

# conftest.py clears the settings variables and runs each test in an empty temporary folder.


@pytest.fixture(autouse=True)
def clear_retention_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAW_PAYLOAD_RETENTION_DAYS", raising=False)


def test_defaults() -> None:
    settings = Settings()
    assert str(settings.gbfs_discovery_url) == OFFICIAL_GBFS_DISCOVERY_URL
    assert settings.database_url is None
    assert settings.raw_payload_retention_days is None


def test_environment_variables_override_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GBFS_DISCOVERY_URL", OTHER_URL)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@localhost/citypulse")
    monkeypatch.setenv("RAW_PAYLOAD_RETENTION_DAYS", "30")
    settings = Settings()
    assert str(settings.gbfs_discovery_url) == OTHER_URL
    assert settings.database_url == "postgresql://user@localhost/citypulse"
    assert settings.raw_payload_retention_days == 30


def test_env_file_in_working_directory_is_read(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(f"GBFS_DISCOVERY_URL={OTHER_URL}\n", encoding="utf-8")
    assert str(Settings().gbfs_discovery_url) == OTHER_URL


def test_environment_variable_wins_over_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(f"GBFS_DISCOVERY_URL={OTHER_URL}\n", encoding="utf-8")
    monkeypatch.setenv("GBFS_DISCOVERY_URL", "https://example.net/gbfs.json")
    assert str(Settings().gbfs_discovery_url) == "https://example.net/gbfs.json"


def test_copied_env_example_keeps_the_defaults(tmp_path: Path) -> None:
    shutil.copyfile(ENV_EXAMPLE, tmp_path / ".env")
    settings = Settings()
    assert str(settings.gbfs_discovery_url) == OFFICIAL_GBFS_DISCOVERY_URL
    assert settings.database_url is None
    assert settings.raw_payload_retention_days is None


def test_empty_retention_means_no_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAW_PAYLOAD_RETENTION_DAYS", "")
    assert Settings().raw_payload_retention_days is None


@pytest.mark.parametrize("value", ["0", "-1"])
def test_nonpositive_retention_is_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("RAW_PAYLOAD_RETENTION_DAYS", value)
    with pytest.raises(ValidationError) as error:
        Settings()
    assert any(
        detail["loc"] == ("raw_payload_retention_days",) and detail["type"] == "greater_than"
        for detail in error.value.errors()
    )


@pytest.mark.parametrize("value", ["not a url", "ftp://example.org/gbfs.json"])
def test_invalid_discovery_url_is_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("GBFS_DISCOVERY_URL", value)
    with pytest.raises(ValidationError):
        Settings()


def test_env_example_documents_every_setting() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if "=" in line and not line.startswith("#")]
    assert {line.split("=", 1)[0] for line in lines} == {
        name.upper() for name in Settings.model_fields
    }
    assert OFFICIAL_GBFS_DISCOVERY_URL in text
