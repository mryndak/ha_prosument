"""Czyste obliczenia net-meteringu (bez zależności od Home Assistanta)."""

from __future__ import annotations

import calendar
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
import math


@dataclass
class Totals:
    """Sumy energii w kWh za wybrany okres."""

    import_raw: float = 0.0
    export_raw: float = 0.0
    import_balanced: float = 0.0
    export_balanced: float = 0.0
    hours: int = 0


@dataclass
class Batch:
    """Partia energii w magazynie; expiry=None oznacza brak terminu ważności."""

    expiry: date | None
    amount: float


@dataclass
class SimResult:
    """Wynik symulacji magazynu za okres."""

    totals: Totals
    saldo: float  # ujemne = energia do zapłaty
    expired: float  # kWh, które przepadły w tym okresie
    batches: list[Batch] = field(default_factory=list)  # partie pozostałe na koniec


def balance(
    imp: dict[float, float | None],
    exp: dict[float, float | None],
    hourly: bool,
) -> Totals:
    """Zsumuj godzinowe przyrosty importu i eksportu.

    imp / exp: przyrost kWh w danej godzinie, kluczem jest początek godziny.
    hourly=True: w każdej godzinie liczy się tylko saldo (bilansowanie godzinowe).
    """
    totals = Totals()
    for ts in imp.keys() | exp.keys():
        di = max(imp.get(ts) or 0.0, 0.0)
        de = max(exp.get(ts) or 0.0, 0.0)
        totals.import_raw += di
        totals.export_raw += de
        if hourly:
            totals.import_balanced += max(di - de, 0.0)
            totals.export_balanced += max(de - di, 0.0)
        else:
            totals.import_balanced += di
            totals.export_balanced += de
        totals.hours += 1
    return totals


def add_months(day: date, months: int) -> date:
    """Dodaj miesiące; ostatni dzień miesiąca zostaje ostatnim dniem miesiąca."""
    year, month0 = divmod(day.month - 1 + months, 12)
    year += day.year
    month = month0 + 1
    last = calendar.monthrange(year, month)[1]
    if day.day == calendar.monthrange(day.year, day.month)[1]:
        return date(year, month, last)
    return date(year, month, min(day.day, last))


def _sorted(batches: list[Batch]) -> list[Batch]:
    """Kopia partii od najstarszej (FIFO); bezterminowe na końcu."""
    return sorted(
        (Batch(b.expiry, b.amount) for b in batches),
        key=lambda b: (b.expiry is None, b.expiry or date.max),
    )


def _consume(pool: list[Batch], deficit: float) -> float:
    """Pokryj niedobór z partii od najstarszej. Zwraca niepokrytą resztę."""
    for batch in pool:
        take = min(batch.amount, deficit)
        batch.amount -= take
        deficit -= take
        if deficit <= 0:
            break
    return max(deficit, 0.0)


def derive_batches(settlements: list[tuple[date, float]], months: int) -> list[Batch]:
    """Podziel magazyn z ostatniego rachunku na partie z terminem ważności.

    settlements: (koniec okresu, magazyn po rozliczeniu), rosnąco po dacie.
    months: ważność energii w miesiącach od końca okresu; 0 = bez wygasania.

    Przyrost magazynu względem poprzedniego rachunku to nowa partia.
    Spadek zdejmuje energię od najstarszej partii. Partia ważna najpóźniej
    do końca rozliczanego okresu nie przechodzi dalej.
    """
    pool: list[Batch] = []
    for period_to, storage in settlements:
        pool = [b for b in pool if b.expiry is None or b.expiry > period_to]
        carried = sum(b.amount for b in pool)
        if storage >= carried:
            if storage - carried > 0:
                expiry = add_months(period_to, months) if months > 0 else None
                pool.append(Batch(expiry, storage - carried))
        else:
            pool = _sorted(pool)
            _consume(pool, carried - storage)
            pool = [b for b in pool if b.amount > 0]
    return _sorted(pool)


def simulate(
    batches: list[Batch],
    imp: dict[float, float | None],
    exp: dict[float, float | None],
    hourly: bool,
    factor: float,
    cut_ts: Callable[[date], float],
    end_ts: float,
) -> SimResult:
    """Policz magazyn za okres z uwzględnieniem wygasania partii.

    cut_ts(data ważności) -> znacznik czasu, od którego partia jest nieważna.
    Uwzględniane są terminy przypadające najpóźniej w end_ts.

    Do terminu ważności partia pokrywa narastający niedobór okresu
    (pobór ponad oddanie × współczynnik); niewykorzystana reszta przepada.
    """
    pool = _sorted(batches)
    cuts = sorted(
        {cut_ts(b.expiry) for b in pool if b.expiry is not None and cut_ts(b.expiry) <= end_ts}
    )
    cum = 0.0  # saldo bieżącego okresu bez starych partii
    expired = 0.0
    prev = -math.inf
    for cut in [*cuts, math.inf]:
        seg = balance(
            {ts: v for ts, v in imp.items() if prev <= ts < cut},
            {ts: v for ts, v in exp.items() if prev <= ts < cut},
            hourly,
        )
        cum += seg.export_balanced * factor - seg.import_balanced
        if cut == math.inf:
            break
        if cum < 0:
            cum = -_consume(pool, -cum)
        for batch in pool:
            if batch.expiry is not None and cut_ts(batch.expiry) <= cut:
                expired += batch.amount
                batch.amount = 0.0
        prev = cut

    saldo = sum(b.amount for b in pool) + cum
    view = _sorted(pool)
    if cum < 0:
        _consume(view, -cum)
    elif cum > 0:
        # nadwyżka bieżącego okresu: jeszcze bez terminu, do czasu rachunku
        view.append(Batch(None, cum))
    return SimResult(
        totals=balance(imp, exp, hourly),
        saldo=saldo,
        expired=expired,
        batches=[b for b in view if b.amount > 1e-9],
    )
