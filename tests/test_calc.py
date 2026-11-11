"""Testy czystych obliczeń. Uruchom: python tests/test_calc.py"""

from datetime import date
import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).parents[1] / "custom_components" / "prosument" / "calc.py"
_spec = importlib.util.spec_from_file_location("calc", _path)
calc = importlib.util.module_from_spec(_spec)
sys.modules["calc"] = calc
_spec.loader.exec_module(calc)

Batch = calc.Batch


def close(a: float, b: float) -> bool:
    return abs(a - b) < 1e-9


def cut(day: date) -> float:
    """Dzień jako liczba; partia jest nieważna od następnego dnia."""
    return float(day.toordinal() + 1)


def ts(day: date) -> float:
    return float(day.toordinal())


def test_hourly_balancing() -> None:
    imp = {1.0: 2.0, 2.0: 0.2}
    exp = {1.0: 0.5, 2.0: 3.0, 3.0: 1.0}
    t = calc.balance(imp, exp, hourly=True)
    assert close(t.import_raw, 2.2) and close(t.export_raw, 4.5)
    assert close(t.import_balanced, 1.5) and close(t.export_balanced, 3.8)
    assert t.hours == 3
    assert close(t.import_raw - t.export_raw, t.import_balanced - t.export_balanced)


def test_no_balancing() -> None:
    t = calc.balance({1.0: 2.0}, {1.0: 0.5}, hourly=False)
    assert close(t.import_balanced, 2.0) and close(t.export_balanced, 0.5)


def test_none_and_negative_ignored() -> None:
    t = calc.balance({1.0: None, 2.0: -5.0}, {1.0: 1.0}, hourly=True)
    assert close(t.import_raw, 0.0) and close(t.export_balanced, 1.0)


def test_add_months() -> None:
    assert calc.add_months(date(2025, 6, 30), 12) == date(2026, 6, 30)
    assert calc.add_months(date(2025, 6, 30), 6) == date(2025, 12, 31)
    assert calc.add_months(date(2025, 12, 31), 6) == date(2026, 6, 30)
    assert calc.add_months(date(2025, 8, 31), 6) == date(2026, 2, 28)
    assert calc.add_months(date(2025, 3, 15), 12) == date(2026, 3, 15)


def test_derive_single_bill() -> None:
    b = calc.derive_batches([(date(2026, 6, 30), 500.0)], 12)
    assert len(b) == 1 and b[0].expiry == date(2027, 6, 30) and close(b[0].amount, 500)


def test_derive_no_expiry() -> None:
    b = calc.derive_batches([(date(2026, 6, 30), 500.0)], 0)
    assert b[0].expiry is None


def test_derive_growth_makes_new_batch() -> None:
    b = calc.derive_batches(
        [(date(2025, 12, 31), 300.0), (date(2026, 6, 30), 800.0)], 12
    )
    assert [(x.expiry, x.amount) for x in b] == [
        (date(2026, 12, 31), 300.0),
        (date(2027, 6, 30), 500.0),
    ]


def test_derive_drop_consumes_oldest() -> None:
    b = calc.derive_batches(
        [
            (date(2025, 6, 30), 300.0),
            (date(2025, 12, 31), 800.0),  # +500
            (date(2026, 3, 31), 600.0),  # -200 z najstarszej
        ],
        12,
    )
    assert [(x.expiry, x.amount) for x in b] == [
        (date(2026, 6, 30), 100.0),
        (date(2026, 12, 31), 500.0),
    ]


def test_derive_expired_batch_not_carried() -> None:
    # partia z 30.06.2025 jest ważna do 30.06.2026, więc nie przechodzi dalej;
    # cały stan z rachunku to nowa energia
    b = calc.derive_batches(
        [(date(2025, 6, 30), 300.0), (date(2026, 6, 30), 400.0)], 12
    )
    assert [(x.expiry, x.amount) for x in b] == [(date(2027, 6, 30), 400.0)]


def test_simulate_without_expiry_in_range() -> None:
    start = date(2026, 7, 1)
    r = calc.simulate(
        [Batch(date(2027, 6, 30), 800.0)],
        {ts(start): 1000.0},
        {ts(start): 500.0},
        False,
        0.8,
        cut,
        ts(date(2026, 9, 1)),
    )
    # 800 + 500*0.8 - 1000 = 200
    assert close(r.saldo, 200.0) and close(r.expired, 0.0)
    assert [(b.expiry, round(b.amount, 6)) for b in r.batches] == [
        (date(2027, 6, 30), 200.0)
    ]


def test_simulate_expiry_loses_unused_part() -> None:
    # partia 300 kWh ważna do 31.12; do tego dnia niedobór 100 -> przepada 200
    r = calc.simulate(
        [Batch(date(2026, 12, 31), 300.0), Batch(date(2027, 6, 30), 500.0)],
        {ts(date(2026, 10, 1)): 100.0, ts(date(2027, 2, 1)): 50.0},
        {},
        False,
        0.8,
        cut,
        ts(date(2027, 3, 1)),
    )
    assert close(r.expired, 200.0)
    assert close(r.saldo, 450.0)  # 500 - 50
    assert [(b.expiry, b.amount) for b in r.batches] == [(date(2027, 6, 30), 450.0)]


def test_simulate_surplus_does_not_save_old_batch() -> None:
    # nadwyżka bieżącego okresu nie przedłuża starej partii
    r = calc.simulate(
        [Batch(date(2026, 12, 31), 300.0)],
        {},
        {ts(date(2026, 8, 1)): 1000.0},
        False,
        0.8,
        cut,
        ts(date(2027, 1, 15)),
    )
    assert close(r.expired, 300.0) and close(r.saldo, 800.0)
    assert [(b.expiry, b.amount) for b in r.batches] == [(None, 800.0)]


def test_simulate_expiry_after_end_is_ignored() -> None:
    r = calc.simulate(
        [Batch(date(2026, 12, 31), 300.0)],
        {},
        {},
        False,
        0.8,
        cut,
        ts(date(2026, 12, 31)),
    )
    assert close(r.expired, 0.0) and close(r.saldo, 300.0)


def test_simulate_deficit_beyond_storage() -> None:
    r = calc.simulate(
        [Batch(date(2026, 12, 31), 100.0)],
        {ts(date(2026, 8, 1)): 400.0},
        {},
        False,
        0.8,
        cut,
        ts(date(2027, 2, 1)),
    )
    assert close(r.saldo, -300.0) and close(r.expired, 0.0) and r.batches == []


def test_simulate_extra_before_meter() -> None:
    # rozliczenie w marcu, miernik od maja: brakujący pobór 300 i oddanie 100
    r = calc.simulate(
        [Batch(date(2027, 3, 31), 800.0)],
        {ts(date(2026, 6, 1)): 50.0},
        {},
        True,
        0.8,
        cut,
        ts(date(2026, 7, 1)),
        extra_import=300.0,
        extra_export=100.0,
    )
    # 800 + 100*0.8 - 300 - 50 = 530
    assert close(r.saldo, 530.0)
    assert close(r.totals.import_raw, 350.0) and close(r.totals.import_balanced, 350.0)
    assert close(r.totals.export_raw, 100.0) and close(r.totals.export_balanced, 100.0)


def test_simulate_extra_counts_before_expiry() -> None:
    # korekta jest liczona na starcie okresu, więc zużywa starą partię przed jej terminem
    r = calc.simulate(
        [Batch(date(2026, 6, 30), 300.0)],
        {},
        {},
        True,
        0.8,
        cut,
        ts(date(2026, 8, 1)),
        extra_import=120.0,
    )
    assert close(r.expired, 180.0) and close(r.saldo, 0.0)


def test_forecast_simple() -> None:
    days, lost = calc.forecast_depletion([Batch(None, 100.0)], 10.0, date(2026, 11, 1))
    assert close(days, 10.0) and close(lost, 0.0)


def test_forecast_not_draining() -> None:
    assert calc.forecast_depletion([Batch(None, 100.0)], 0.0, date(2026, 11, 1)) == (None, 0.0)
    assert calc.forecast_depletion([Batch(None, 100.0)], -3.0, date(2026, 11, 1))[0] is None


def test_forecast_empty_storage() -> None:
    days, lost = calc.forecast_depletion([], 10.0, date(2026, 11, 1))
    assert close(days, 0.0) and close(lost, 0.0)


def test_forecast_expiry_shortens_it() -> None:
    today = date(2026, 11, 1)
    # partia 100 kWh ważna jeszcze 5 dni (do 5.11 włącznie): zużyjemy 50, 50 przepada
    days, lost = calc.forecast_depletion(
        [Batch(date(2026, 11, 5), 100.0), Batch(date(2027, 6, 30), 60.0)], 10.0, today
    )
    assert close(days, 11.0) and close(lost, 50.0)


def test_forecast_batch_expiring_before_reached() -> None:
    today = date(2026, 11, 1)
    # pierwsza partia starcza na 10 dni, druga wygasa po 3 dniach, zanim do niej dojdziemy
    days, lost = calc.forecast_depletion(
        [Batch(date(2026, 11, 3), 0.0), Batch(date(2026, 12, 31), 100.0), Batch(date(2026, 11, 5), 40.0)],
        10.0,
        today,
    )
    # FIFO po dacie: partia do 5.11 idzie pierwsza (40 kWh = 4 dni), potem 100 kWh = 10 dni
    assert close(days, 14.0) and close(lost, 0.0)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
