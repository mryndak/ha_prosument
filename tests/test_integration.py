"""Testy całej integracji na prawdziwym Home Assistancie z rejestratorem."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from custom_components.prosument.const import LIVE_COOLDOWN_SECONDS
from custom_components.prosument.sensor import SENSORS

from .conftest import (
    EXPORT,
    IMPORT,
    NOW,
    UTC,
    add_statistics,
    sensor,
    set_meter,
    setup_entry,
    value,
)

# Godziny w bieżącym okresie (po 1.11.2026), ale przed "teraz".
IN_PERIOD = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)


async def _advance(hass: HomeAssistant, freezer, seconds: float) -> None:
    """Przesuń zegar; najpierw daj integracji przetworzyć oczekujące zmiany stanów."""
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    # odświeżenie koordynatora biegnie jako zadanie w tle, więc czekamy i na nie
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_all_sensors_created_without_statistics(ha: HomeAssistant) -> None:
    """Świeża instalacja bez żadnych statystyk: nic się nie wywraca."""
    set_meter(ha, IMPORT, 1000)
    set_meter(ha, EXPORT, 500)
    entry = await setup_entry(ha)

    assert entry.state is ConfigEntryState.LOADED
    for description in SENSORS:
        assert sensor(ha, entry, description.key).state != STATE_UNAVAILABLE

    assert value(ha, entry, "storage_left") == 100.0
    assert value(ha, entry, "start_storage") == 100.0
    assert value(ha, entry, "to_pay_kwh") == 0.0
    assert value(ha, entry, "cost") == 0.0
    assert value(ha, entry, "hour_balance") == 0.0
    # za mało danych na prognozę
    assert sensor(ha, entry, "forecast_date").state == STATE_UNKNOWN
    assert sensor(ha, entry, "forecast_date").attributes["trend"] == "za mało danych"


async def test_storage_uses_hourly_balancing(ha: HomeAssistant) -> None:
    """Magazyn = rachunek + oddana × współczynnik − pobrana, po bilansowaniu."""
    # godz. 1: pobór 2.0, oddanie 0.5 -> pobór netto 1.5
    # godz. 2: pobór 0.2, oddanie 3.0 -> oddanie netto 2.8
    await add_statistics(ha, IMPORT, IN_PERIOD, [2.0, 0.2])
    await add_statistics(ha, EXPORT, IN_PERIOD, [0.5, 3.0])
    set_meter(ha, IMPORT, 1002.2)
    set_meter(ha, EXPORT, 1003.5)
    entry = await setup_entry(ha)

    assert value(ha, entry, "import_balanced") == pytest.approx(1.5)
    assert value(ha, entry, "export_balanced") == pytest.approx(2.8)
    assert value(ha, entry, "import_raw") == pytest.approx(2.2)
    assert value(ha, entry, "export_raw") == pytest.approx(3.5)
    assert value(ha, entry, "storage_left") == pytest.approx(100 + 2.8 * 0.8 - 1.5)


async def test_balancing_can_be_disabled(ha: HomeAssistant) -> None:
    await add_statistics(ha, IMPORT, IN_PERIOD, [2.0, 0.2])
    await add_statistics(ha, EXPORT, IN_PERIOD, [0.5, 3.0])
    entry = await setup_entry(ha, hourly_balancing=False)

    assert value(ha, entry, "import_balanced") == pytest.approx(2.2)
    assert value(ha, entry, "storage_left") == pytest.approx(100 + 3.5 * 0.8 - 2.2)


async def test_statistics_before_bill_are_ignored(ha: HomeAssistant) -> None:
    """Liczenie startuje od północy czasu polskiego dnia po końcu okresu."""
    # 1.11 22:00 UTC = 1.11 23:00 w Polsce: jeszcze stary okres
    # 1.11 23:00 UTC = 2.11 00:00 w Polsce: już nowy okres
    await add_statistics(ha, IMPORT, datetime(2026, 11, 1, 22, tzinfo=UTC), [7.0, 3.0])
    entry = await setup_entry(ha)

    assert value(ha, entry, "import_raw") == pytest.approx(3.0)
    assert value(ha, entry, "storage_left") == pytest.approx(97.0)


async def test_energy_to_pay_and_cost(ha: HomeAssistant) -> None:
    await add_statistics(ha, IMPORT, IN_PERIOD, [150.0])
    entry = await setup_entry(ha)

    assert value(ha, entry, "storage_left") == 0.0
    assert value(ha, entry, "saldo") == pytest.approx(-50.0)
    assert value(ha, entry, "to_pay_kwh") == pytest.approx(50.0)
    assert value(ha, entry, "cost") == pytest.approx(60.0)


async def test_statistics_in_wh_are_converted(ha: HomeAssistant) -> None:
    await add_statistics(ha, IMPORT, IN_PERIOD, [4000.0], unit="Wh")
    entry = await setup_entry(ha)

    assert value(ha, entry, "import_raw") == pytest.approx(4.0)
    assert value(ha, entry, "storage_left") == pytest.approx(96.0)


async def test_correction_before_meter(ha: HomeAssistant) -> None:
    """Pobór i oddanie sprzed uruchomienia miernika wchodzą do magazynu."""
    entry = await setup_entry(
        ha,
        settlements=[
            {
                "period_to": "2026-11-01",
                "storage": 100.0,
                "extra_import": 30.0,
                "extra_export": 10.0,
            }
        ],
    )
    assert value(ha, entry, "storage_left") == pytest.approx(100 + 10 * 0.8 - 30)
    assert value(ha, entry, "import_raw") == pytest.approx(30.0)


async def test_expiry_batches_from_two_bills(ha: HomeAssistant) -> None:
    """Dwa rachunki dają dwie partie; najstarsza wygasa pierwsza."""
    entry = await setup_entry(
        ha,
        settlements=[
            {"period_to": "2025-12-31", "storage": 300.0},
            {"period_to": "2026-06-30", "storage": 800.0},
        ],
    )
    assert sensor(ha, entry, "next_expiry_date").state == "2026-12-31"
    assert value(ha, entry, "next_expiry_kwh") == pytest.approx(300.0)
    assert value(ha, entry, "expired_kwh") == 0.0
    batches = sensor(ha, entry, "storage_left").attributes["partie"]
    assert batches == [
        {"wazna_do": "2026-12-31", "kwh": 300.0},
        {"wazna_do": "2027-06-30", "kwh": 500.0},
    ]


async def test_expired_energy_is_lost(ha: HomeAssistant) -> None:
    """Partia, której termin minął, znika z magazynu."""
    entry = await setup_entry(
        ha,
        settlements=[
            {"period_to": "2025-06-30", "storage": 300.0},
            {"period_to": "2025-12-31", "storage": 800.0},
        ],
    )
    # partia 300 kWh była ważna do 30.06.2026 i przepadła; zostaje 500 kWh
    assert value(ha, entry, "expired_kwh") == pytest.approx(300.0)
    assert value(ha, entry, "storage_left") == pytest.approx(500.0)
    assert sensor(ha, entry, "next_expiry_date").state == "2026-12-31"


async def test_expiry_can_be_disabled(ha: HomeAssistant) -> None:
    entry = await setup_entry(
        ha,
        expiry_months=0,
        settlements=[
            {"period_to": "2025-06-30", "storage": 300.0},
            {"period_to": "2025-12-31", "storage": 800.0},
        ],
    )
    assert value(ha, entry, "storage_left") == pytest.approx(800.0)
    assert sensor(ha, entry, "next_expiry_date").state == STATE_UNKNOWN


async def test_forecast_from_last_seven_days(ha: HomeAssistant) -> None:
    """Prognoza: średni ubytek z 7 dni i data wyczerpania magazynu."""
    # 168 godzin przed "teraz", w każdej pobór 0.5 kWh -> 12 kWh na dobę
    start = NOW.replace(minute=0) - timedelta(days=7)
    await add_statistics(ha, IMPORT, start, [0.5] * 168)
    await add_statistics(ha, EXPORT, start, [0.0] * 168)
    entry = await setup_entry(
        ha, settlements=[{"period_to": "2026-11-01", "storage": 534.0}]
    )

    assert value(ha, entry, "storage_left") == pytest.approx(450.0)
    assert value(ha, entry, "daily_drain") == pytest.approx(12.0)
    assert value(ha, entry, "forecast_days") == pytest.approx(37.5)
    # 11.11 15:30 czasu polskiego + 37,5 dnia = 19.12
    assert sensor(ha, entry, "forecast_date").state == "2026-12-19"
    assert sensor(ha, entry, "forecast_date").attributes["trend"] == "magazyn maleje"


async def test_forecast_unknown_when_storage_grows(ha: HomeAssistant) -> None:
    start = NOW.replace(minute=0) - timedelta(days=7)
    await add_statistics(ha, IMPORT, start, [0.1] * 168)
    await add_statistics(ha, EXPORT, start, [1.0] * 168)
    entry = await setup_entry(ha)

    assert value(ha, entry, "daily_drain") < 0
    assert sensor(ha, entry, "forecast_days").state == STATE_UNKNOWN
    assert sensor(ha, entry, "forecast_date").attributes["trend"] == "magazyn nie maleje"


async def test_bill_check_against_meter(ha: HomeAssistant) -> None:
    """Porównanie ostatniego rachunku z licznikiem za okres rachunku."""
    await add_statistics(ha, IMPORT, datetime(2026, 8, 1, 10, tzinfo=UTC), [200.0])
    await add_statistics(ha, EXPORT, datetime(2026, 8, 1, 12, tzinfo=UTC), [300.0])
    entry = await setup_entry(
        ha,
        settlements=[
            {"period_to": "2026-06-30", "storage": 100.0},
            {
                "period_from": "2026-07-01",
                "period_to": "2026-11-01",
                "storage": 150.0,
                "billed_import": 205.0,
                "billed_export": 290.0,
            },
        ],
    )
    # licznik: 100 + 300 × 0.8 − 200 = 140; rachunek mówi 150
    assert value(ha, entry, "diff_storage") == pytest.approx(10.0)
    assert value(ha, entry, "diff_import") == pytest.approx(5.0)
    assert value(ha, entry, "diff_export") == pytest.approx(-10.0)
    attributes = sensor(ha, entry, "diff_storage").attributes
    assert attributes["licznik_magazyn_wyliczony"] == 140.0
    # okres rachunku nie wchodzi do okresu bieżącego
    assert value(ha, entry, "storage_left") == pytest.approx(150.0)


async def test_live_hour_balance(ha: HomeAssistant, freezer) -> None:
    """Zmiany liczników w trakcie godziny trafiają do salda i do magazynu."""
    set_meter(ha, IMPORT, 1000.0)
    set_meter(ha, EXPORT, 500.0)
    entry = await setup_entry(ha)
    assert value(ha, entry, "hour_balance") == 0.0

    set_meter(ha, IMPORT, 1000.2)
    set_meter(ha, EXPORT, 501.2)
    await _advance(ha, freezer, LIVE_COOLDOWN_SECONDS + 1)

    assert value(ha, entry, "hour_balance") == pytest.approx(1.0)
    attributes = sensor(ha, entry, "hour_balance").attributes
    assert attributes["pobrane_w_tej_godzinie"] == pytest.approx(0.2)
    assert attributes["oddane_w_tej_godzinie"] == pytest.approx(1.2)
    # nadwyżka 1.0 kWh z bieżącej godziny, po współczynniku 0.8
    assert value(ha, entry, "storage_left") == pytest.approx(100.8)


async def test_live_hour_in_wh(ha: HomeAssistant, freezer) -> None:
    set_meter(ha, IMPORT, 1_000_000, unit="Wh")
    set_meter(ha, EXPORT, 500_000, unit="Wh")
    entry = await setup_entry(ha)

    set_meter(ha, IMPORT, 1_000_500, unit="Wh")
    await _advance(ha, freezer, LIVE_COOLDOWN_SECONDS + 1)

    assert value(ha, entry, "hour_balance") == pytest.approx(-0.5)
    assert value(ha, entry, "storage_left") == pytest.approx(99.5)


async def test_hour_rollover_keeps_finished_hour(ha: HomeAssistant, freezer) -> None:
    """Po pełnej godzinie saldo się zeruje, a zamknięta godzina zostaje w magazynie."""
    set_meter(ha, IMPORT, 1000.0)
    set_meter(ha, EXPORT, 500.0)
    entry = await setup_entry(ha)
    set_meter(ha, IMPORT, 1003.0)
    await _advance(ha, freezer, LIVE_COOLDOWN_SECONDS + 1)
    assert value(ha, entry, "storage_left") == pytest.approx(97.0)

    freezer.move_to(datetime(2026, 11, 11, 15, 0, 0, tzinfo=UTC))
    async_fire_time_changed(ha)
    await ha.async_block_till_done(wait_background_tasks=True)
    await _advance(ha, freezer, LIVE_COOLDOWN_SECONDS + 1)

    assert value(ha, entry, "hour_balance") == 0.0
    assert value(ha, entry, "storage_left") == pytest.approx(97.0)

    # nowa godzina liczy się od stanu z jej początku
    set_meter(ha, IMPORT, 1004.0)
    await _advance(ha, freezer, LIVE_COOLDOWN_SECONDS + 1)
    assert value(ha, entry, "hour_balance") == pytest.approx(-1.0)
    assert value(ha, entry, "storage_left") == pytest.approx(96.0)


async def test_finished_hour_not_counted_twice(ha: HomeAssistant, freezer) -> None:
    """Gdy statystyki zamkniętej godziny się pojawią, nie dublują się z danymi na żywo."""
    set_meter(ha, IMPORT, 1000.0)
    set_meter(ha, EXPORT, 500.0)
    entry = await setup_entry(ha)
    set_meter(ha, IMPORT, 1003.0)
    await _advance(ha, freezer, LIVE_COOLDOWN_SECONDS + 1)

    freezer.move_to(datetime(2026, 11, 11, 15, 0, 0, tzinfo=UTC))
    async_fire_time_changed(ha)
    await ha.async_block_till_done(wait_background_tasks=True)

    await _advance(ha, freezer, LIVE_COOLDOWN_SECONDS + 1)
    assert value(ha, entry, "import_raw") == pytest.approx(3.0)

    # rejestrator zapisuje statystykę godziny 14:00–15:00; różni się nieco od
    # odczytu na żywo (3.5 zamiast 3.0), żeby było widać, które dane wygrały
    await add_statistics(ha, IMPORT, datetime(2026, 11, 11, 14, tzinfo=UTC), [3.5])
    # odświeżenie statystyk co 10 minut
    await _advance(ha, freezer, 11 * 60)

    # statystyka zastąpiła odczyt na żywo, a nie dodała się do niego
    assert value(ha, entry, "import_raw") == pytest.approx(3.5)
    assert value(ha, entry, "storage_left") == pytest.approx(96.5)


async def test_meter_unavailable(ha: HomeAssistant, freezer) -> None:
    """Niedostępny miernik nie psuje integracji; saldo godziny jest nieznane."""
    ha.states.async_set(IMPORT, STATE_UNAVAILABLE)
    ha.states.async_set(EXPORT, STATE_UNAVAILABLE)
    entry = await setup_entry(ha)

    assert entry.state is ConfigEntryState.LOADED
    assert sensor(ha, entry, "hour_balance").state == STATE_UNKNOWN
    assert value(ha, entry, "storage_left") == 100.0

    # miernik wraca: liczenie startuje od pierwszego odczytu
    set_meter(ha, IMPORT, 1000.0)
    set_meter(ha, EXPORT, 500.0)
    await _advance(ha, freezer, LIVE_COOLDOWN_SECONDS + 1)
    assert value(ha, entry, "hour_balance") == 0.0
    assert sensor(ha, entry, "hour_balance").attributes["niepelna_godzina"] is True


async def test_meter_reset_does_not_create_energy(ha: HomeAssistant, freezer) -> None:
    """Wyzerowanie licznika nie jest liczone jako pobór ani oddanie."""
    set_meter(ha, IMPORT, 1000.0)
    set_meter(ha, EXPORT, 500.0)
    entry = await setup_entry(ha)

    set_meter(ha, IMPORT, 0.0)
    await _advance(ha, freezer, LIVE_COOLDOWN_SECONDS + 1)
    assert value(ha, entry, "hour_balance") == 0.0
    assert value(ha, entry, "storage_left") == 100.0


async def test_hour_baseline_from_statistics_after_restart(ha: HomeAssistant) -> None:
    """Po restarcie w środku godziny stan z jej początku pochodzi ze statystyk."""
    # poprzednia godzina (13:00–14:00 UTC) zakończyła się stanem licznika 1005 / 505
    previous = datetime(2026, 11, 11, 13, tzinfo=UTC)
    await add_statistics(ha, IMPORT, previous, [5.0])
    await add_statistics(ha, EXPORT, previous, [5.0], offset=500.0)
    # od początku bieżącej godziny przybyło 0.4 poboru i 1.0 oddania
    set_meter(ha, IMPORT, 1005.4)
    set_meter(ha, EXPORT, 506.0)
    entry = await setup_entry(ha)

    assert value(ha, entry, "hour_balance") == pytest.approx(0.6)
    assert sensor(ha, entry, "hour_balance").attributes["niepelna_godzina"] is False


async def test_unload_and_reload(ha: HomeAssistant, freezer) -> None:
    set_meter(ha, IMPORT, 1000.0)
    set_meter(ha, EXPORT, 500.0)
    entry = await setup_entry(ha)

    assert await ha.config_entries.async_unload(entry.entry_id)
    await ha.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert sensor(ha, entry, "storage_left").state == STATE_UNAVAILABLE

    # po wyładowaniu integracja nie reaguje już na zmiany liczników ani zegar
    set_meter(ha, IMPORT, 1001.0)
    await _advance(ha, freezer, 3700)

    assert await ha.config_entries.async_setup(entry.entry_id)
    await ha.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert sensor(ha, entry, "storage_left").state != STATE_UNAVAILABLE


async def test_options_change_reloads_entry(ha: HomeAssistant) -> None:
    """Zmiana opcji (np. nowy rachunek) przelicza sensory."""
    entry = await setup_entry(ha)
    assert value(ha, entry, "storage_left") == 100.0

    new_options = dict(entry.options)
    new_options["settlements"] = [{"period_to": "2026-11-01", "storage": 250.0}]
    ha.config_entries.async_update_entry(entry, options=new_options)
    await ha.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert value(ha, entry, "storage_left") == 250.0
    assert value(ha, entry, "start_storage") == 250.0


async def test_bill_dated_in_the_future(ha: HomeAssistant, freezer) -> None:
    """Rachunek z datą późniejszą niż dziś: nic nie jest jeszcze doliczane."""
    set_meter(ha, IMPORT, 1000.0)
    set_meter(ha, EXPORT, 500.0)
    entry = await setup_entry(
        ha, settlements=[{"period_to": "2026-11-30", "storage": 100.0}]
    )
    set_meter(ha, IMPORT, 1002.0)
    await _advance(ha, freezer, LIVE_COOLDOWN_SECONDS + 1)

    assert value(ha, entry, "storage_left") == 100.0
    assert value(ha, entry, "hour_balance") == pytest.approx(-2.0)
