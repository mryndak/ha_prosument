"""Testy formularzy: pierwsza konfiguracja i menu „Konfiguruj”."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.prosument.const import DOMAIN

from .conftest import EXPORT, IMPORT, set_meter, setup_entry, value

SETTINGS: dict[str, Any] = {
    "import_entity": IMPORT,
    "export_entity": EXPORT,
    "factor": 0.8,
    "price": 1.2,
    "expiry_months": 12,
    "forecast_days": 7,
    "hourly_balancing": True,
}


async def _start(hass: HomeAssistant) -> dict[str, Any]:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_full_setup_flow(ha: HomeAssistant) -> None:
    """Dwa kroki: ustawienia, potem rachunek; powstaje działający wpis."""
    set_meter(ha, IMPORT, 1000)
    set_meter(ha, EXPORT, 500)

    result = await _start(ha)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await ha.config_entries.flow.async_configure(result["flow_id"], SETTINGS)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "settlement"

    result = await ha.config_entries.flow.async_configure(
        result["flow_id"], {"period_to": "2026-11-01", "storage": 321.5}
    )
    await ha.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Prosument"

    entry = ha.config_entries.async_entries(DOMAIN)[0]
    assert entry.state is ConfigEntryState.LOADED
    assert entry.options["settlements"] == [
        {"period_to": "2026-11-01", "storage": 321.5}
    ]
    assert value(ha, entry, "storage_left") == 321.5


async def test_same_entity_rejected(ha: HomeAssistant) -> None:
    set_meter(ha, IMPORT, 1000)
    result = await _start(ha)
    result = await ha.config_entries.flow.async_configure(
        result["flow_id"], {**SETTINGS, "export_entity": IMPORT}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "same_entity"}


async def test_entity_without_statistics_rejected(ha: HomeAssistant) -> None:
    """Sensor bez state_class nie ma statystyk, więc nie nadaje się na licznik."""
    set_meter(ha, IMPORT, 1000)
    ha.states.async_set(EXPORT, "5", {"unit_of_measurement": "kWh"})
    result = await _start(ha)
    result = await ha.config_entries.flow.async_configure(result["flow_id"], SETTINGS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"export_entity": "no_statistics"}


async def test_period_start_after_end_rejected(ha: HomeAssistant) -> None:
    set_meter(ha, IMPORT, 1000)
    set_meter(ha, EXPORT, 500)
    result = await _start(ha)
    result = await ha.config_entries.flow.async_configure(result["flow_id"], SETTINGS)
    result = await ha.config_entries.flow.async_configure(
        result["flow_id"],
        {"period_from": "2026-12-01", "period_to": "2026-11-01", "storage": 1.0},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "dates"}


async def _options_step(hass: HomeAssistant, entry, step: str) -> dict[str, Any]:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert step in result["menu_options"]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def test_options_add_bill(ha: HomeAssistant) -> None:
    entry = await setup_entry(ha)
    result = await _options_step(ha, entry, "add_settlement")
    assert result["type"] is FlowResultType.FORM

    result = await ha.config_entries.options.async_configure(
        result["flow_id"], {"period_to": "2026-11-05", "storage": 420.0}
    )
    await ha.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [s["period_to"] for s in entry.options["settlements"]] == [
        "2026-11-01",
        "2026-11-05",
    ]
    # liczenie przeszło na nowy rachunek
    assert value(ha, entry, "start_storage") == 420.0


async def test_options_edit_last_bill_changes_date(ha: HomeAssistant) -> None:
    """Poprawa daty końca okresu w ostatnim rachunku zastępuje wpis, a nie dodaje nowy."""
    entry = await setup_entry(ha)
    result = await _options_step(ha, entry, "edit_last")
    assert result["type"] is FlowResultType.FORM

    result = await ha.config_entries.options.async_configure(
        result["flow_id"], {"period_to": "2026-10-31", "storage": 110.0}
    )
    await ha.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["settlements"] == [
        {"period_to": "2026-10-31", "storage": 110.0}
    ]
    assert value(ha, entry, "start_storage") == 110.0


async def test_options_remove_last_bill(ha: HomeAssistant) -> None:
    entry = await setup_entry(
        ha,
        settlements=[
            {"period_to": "2026-06-30", "storage": 50.0},
            {"period_to": "2026-11-01", "storage": 100.0},
        ],
    )
    result = await _options_step(ha, entry, "remove_last")
    assert result["type"] is FlowResultType.FORM

    result = await ha.config_entries.options.async_configure(result["flow_id"], {})
    await ha.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["settlements"] == [
        {"period_to": "2026-06-30", "storage": 50.0}
    ]


async def test_options_cannot_remove_only_bill(ha: HomeAssistant) -> None:
    entry = await setup_entry(ha)
    result = await _options_step(ha, entry, "remove_last")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "only_one_settlement"


async def test_options_settings(ha: HomeAssistant) -> None:
    set_meter(ha, IMPORT, 1000)
    set_meter(ha, EXPORT, 500)
    entry = await setup_entry(ha)
    result = await _options_step(ha, entry, "settings")
    assert result["type"] is FlowResultType.FORM

    result = await ha.config_entries.options.async_configure(
        result["flow_id"], {**SETTINGS, "factor": 0.7, "forecast_days": 14}
    )
    await ha.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["factor"] == 0.7
    assert entry.options["forecast_days"] == 14
    # rachunki zostają nietknięte
    assert entry.options["settlements"] == [
        {"period_to": "2026-11-01", "storage": 100.0}
    ]
    assert entry.state is ConfigEntryState.LOADED
