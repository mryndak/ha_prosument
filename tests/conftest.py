"""Wspólne elementy testów uruchamianych na prawdziwym Home Assistancie."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.prosument.const import DOMAIN

UTC = timezone.utc
IMPORT = "sensor.grid_import"
EXPORT = "sensor.grid_export"

# "Teraz" we wszystkich testach: środa 11.11.2026, 15:30 czasu polskiego.
NOW = datetime(2026, 11, 11, 14, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _enable_custom_integrations(
    recorder_mock: Any, enable_custom_integrations: None
) -> None:
    """Pozwól Home Assistantowi ładować integrację z custom_components.

    Rejestrator musi wystartować przed Home Assistantem, dlatego jest tu pierwszy.
    """


@pytest.fixture(autouse=True)
def _no_integration_errors(caplog: pytest.LogCaptureFixture):
    """Każdy test pada, jeśli integracja zapisała błąd w logu."""
    yield
    errors = [
        record
        for record in caplog.get_records("call")
        if record.levelno >= logging.ERROR
        and ("prosument" in record.name or "prosument" in record.getMessage())
    ]
    assert not errors, [record.getMessage() for record in errors]


@pytest.fixture
async def ha(recorder_mock: Any, hass: HomeAssistant, freezer) -> HomeAssistant:
    """Home Assistant z rejestratorem, strefą polską i zamrożonym czasem."""
    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("Europe/Warsaw")
    yield hass
    # Wyładuj integrację, zanim zegar zostanie odmrożony: inaczej jej timery
    # odpaliłyby zapytania do rejestratora w trakcie jego zamykania.
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def set_meter(
    hass: HomeAssistant, entity_id: str, value: float | str, unit: str = "kWh"
) -> None:
    """Ustaw stan licznika energii tak, jak robi to integracja miernika."""
    hass.states.async_set(
        entity_id,
        str(value),
        {
            "unit_of_measurement": unit,
            "device_class": "energy",
            "state_class": "total_increasing",
        },
    )


async def add_statistics(
    hass: HomeAssistant,
    entity_id: str,
    start: datetime,
    hourly: list[float],
    unit: str = "kWh",
    offset: float = 1000.0,
) -> None:
    """Wpisz do rejestratora godzinowe przyrosty licznika, zaczynając od `start`."""
    metadata = {
        "has_mean": False,
        "mean_type": StatisticMeanType.NONE,
        "has_sum": True,
        "name": None,
        "source": "recorder",
        "statistic_id": entity_id,
        "unit_class": "energy",
        "unit_of_measurement": unit,
    }
    total = 0.0
    rows = []
    for index, value in enumerate(hourly):
        total += value
        rows.append(
            {
                "start": start + timedelta(hours=index),
                "state": offset + total,
                "sum": total,
            }
        )
    async_import_statistics(hass, metadata, rows)
    await async_wait_recording_done(hass)


def options(**overrides: Any) -> dict[str, Any]:
    """Ustawienia wpisu: jeden rachunek do 1.11.2026 z magazynem 100 kWh."""
    base: dict[str, Any] = {
        "import_entity": IMPORT,
        "export_entity": EXPORT,
        "factor": 0.8,
        "price": 1.2,
        "expiry_months": 12,
        "forecast_days": 7,
        "hourly_balancing": True,
        "settlements": [{"period_to": "2026-11-01", "storage": 100.0}],
    }
    base.update(overrides)
    return base


async def setup_entry(hass: HomeAssistant, **overrides: Any) -> MockConfigEntry:
    """Dodaj wpis konfiguracji i poczekaj, aż integracja wystartuje."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Prosument", data={}, options=options(**overrides)
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def sensor(hass: HomeAssistant, entry: MockConfigEntry, key: str):
    """Stan sensora integracji wyszukany po kluczu, niezależnie od nazwy encji."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert entity_id is not None, f"brak sensora {key}"
    state = hass.states.get(entity_id)
    assert state is not None, f"sensor {key} nie ma stanu"
    return state


def value(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> float:
    return float(sensor(hass, entry, key).state)
