"""Koordynator: liczy stan magazynu ze statystyk godzinowych rejestratora."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .calc import (
    Batch,
    SimResult,
    balance,
    derive_batches,
    forecast_depletion,
    simulate,
)
from .const import (
    CONF_EXPIRY_MONTHS,
    CONF_EXPORT,
    CONF_FACTOR,
    CONF_FORECAST_DAYS,
    CONF_HOURLY,
    CONF_IMPORT,
    CONF_PRICE,
    CONF_SETTLEMENTS,
    DEFAULT_EXPIRY_MONTHS,
    DEFAULT_FACTOR,
    DEFAULT_FORECAST_DAYS,
    DEFAULT_PRICE,
    DOMAIN,
    MIN_FORECAST_HOURS,
    S_EXPORT,
    S_EXTRA_EXPORT,
    S_EXTRA_IMPORT,
    S_FROM,
    S_IMPORT,
    S_STORAGE,
    S_TO,
    UPDATE_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

Series = tuple[dict[float, float | None], dict[float, float | None]]


def _day_start(day: date) -> datetime:
    """Północ czasu lokalnego danego dnia, w UTC."""
    return dt_util.as_utc(dt_util.start_of_local_day(day))


def _cut_ts(expiry: date) -> float:
    """Partia jest ważna do końca dnia `expiry` czasu lokalnego."""
    return _day_start(expiry + timedelta(days=1)).timestamp()


def _batches_attr(batches: list[Batch]) -> list[dict[str, Any]]:
    return [
        {
            "wazna_do": b.expiry.isoformat() if b.expiry else "biezacy okres",
            "kwh": round(b.amount, 2),
        }
        for b in batches
    ]


class ProsumentCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Przelicza dane od ostatniego rozliczenia i kontrolę ostatniego rachunku."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=UPDATE_MINUTES),
        )
        self.entry = entry

    @property
    def conf(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    async def _series(self, start: datetime, end: datetime | None) -> Series:
        """Godzinowe przyrosty importu i eksportu (kWh) z rejestratora."""
        conf = self.conf
        imp_id: str = conf[CONF_IMPORT]
        exp_id: str = conf[CONF_EXPORT]
        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            end,
            {imp_id, exp_id},
            "hour",
            {"energy": UnitOfEnergy.KILO_WATT_HOUR},
            {"change"},
        )
        imp = {row["start"]: row.get("change") for row in stats.get(imp_id, [])}
        exp = {row["start"]: row.get("change") for row in stats.get(exp_id, [])}
        return imp, exp

    def _simulate(
        self,
        batches: list[Batch],
        series: Series,
        end: datetime,
        settlement: dict[str, Any] | None,
    ) -> SimResult:
        """settlement: rachunek otwierający okres (źródło korekty początkowej)."""
        settlement = settlement or {}
        conf = self.conf
        return simulate(
            batches,
            series[0],
            series[1],
            conf.get(CONF_HOURLY, True),
            conf.get(CONF_FACTOR, DEFAULT_FACTOR),
            _cut_ts,
            end.timestamp(),
            settlement.get(S_EXTRA_IMPORT) or 0.0,
            settlement.get(S_EXTRA_EXPORT) or 0.0,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        conf = self.conf
        factor: float = conf.get(CONF_FACTOR, DEFAULT_FACTOR)
        price: float = conf.get(CONF_PRICE, DEFAULT_PRICE)
        months = int(conf.get(CONF_EXPIRY_MONTHS, DEFAULT_EXPIRY_MONTHS))
        settlements = sorted(conf.get(CONF_SETTLEMENTS, []), key=lambda s: s[S_TO])
        pairs = [(date.fromisoformat(s[S_TO]), s[S_STORAGE]) for s in settlements]
        last = settlements[-1]
        prev = settlements[-2] if len(settlements) > 1 else None
        last_to = pairs[-1][0]

        # --- Okres bieżący: od dnia po ostatnim rozliczeniu do teraz ---
        start = _day_start(last_to + timedelta(days=1))
        res = self._simulate(
            derive_batches(pairs, months),
            await self._series(start, None),
            dt_util.utcnow(),
            last,
        )
        cur = res.totals
        to_pay = max(-res.saldo, 0.0)
        dated = [b for b in res.batches if b.expiry is not None]

        data: dict[str, Any] = {
            "storage_left": max(res.saldo, 0.0),
            "to_pay_kwh": to_pay,
            "cost": to_pay * price,
            "saldo": res.saldo,
            "import_balanced": cur.import_balanced,
            "export_balanced": cur.export_balanced,
            "import_raw": cur.import_raw,
            "export_raw": cur.export_raw,
            "expired_kwh": res.expired,
            "next_expiry_date": dated[0].expiry if dated else None,
            "next_expiry_kwh": dated[0].amount if dated else None,
            "batches": _batches_attr(res.batches),
            "hours": cur.hours,
            "period_start": (last_to + timedelta(days=1)).isoformat(),
            "start_storage": last[S_STORAGE],
            "bill_period_to": last_to.isoformat(),
            "bill_period_from": last.get(S_FROM),
            "extra_import": last.get(S_EXTRA_IMPORT) or 0.0,
            "extra_export": last.get(S_EXTRA_EXPORT) or 0.0,
            "factor": factor,
            "price": price,
            "expiry_months": months,
            "diff_import": None,
            "diff_export": None,
            "diff_storage": None,
            "check": {},
        }

        # --- Prognoza: średni ubytek magazynu z ostatnich dni ---
        window_days = int(conf.get(CONF_FORECAST_DAYS, DEFAULT_FORECAST_DAYS))
        now = dt_util.utcnow()
        window_start = now.replace(minute=0, second=0, microsecond=0) - timedelta(
            days=window_days
        )
        win_imp, win_exp = await self._series(window_start, None)
        win = balance(win_imp, win_exp, conf.get(CONF_HOURLY, True))
        forecast: dict[str, Any] = {
            "okno_dni": window_days,
            "godzin_w_oknie": win.hours,
        }
        data.update(
            {
                "daily_drain": None,
                "forecast_days": None,
                "forecast_date": None,
                "forecast": forecast,
            }
        )
        if win.hours >= MIN_FORECAST_HOURS:
            per_day = 24 / win.hours
            drain = (win.import_balanced - win.export_balanced * factor) * per_day
            data["daily_drain"] = drain
            forecast["sredni_pobor_dzienny"] = round(win.import_balanced * per_day, 2)
            forecast["srednie_oddanie_dzienne"] = round(
                win.export_balanced * per_day, 2
            )
            days, lost = forecast_depletion(res.batches, drain, dt_util.now().date())
            if days is None:
                forecast["trend"] = "magazyn nie maleje"
            else:
                forecast["trend"] = "magazyn maleje"
                forecast["utracone_przez_wygasniecie"] = round(lost, 2)
                data["forecast_days"] = days
                data["forecast_date"] = (dt_util.now() + timedelta(days=days)).date()
        else:
            forecast["trend"] = "za mało danych"

        # --- Kontrola ostatniego rachunku: rachunek kontra licznik ---
        if last.get(S_FROM):
            check_from: date | None = date.fromisoformat(last[S_FROM])
        elif prev:
            check_from = pairs[-2][0] + timedelta(days=1)
        else:
            check_from = None

        if check_from is not None:
            check_end = _day_start(last_to + timedelta(days=1))
            sim = self._simulate(
                derive_batches(pairs[:-1], months),
                await self._series(_day_start(check_from), check_end),
                check_end,
                prev,
            )
            chk = sim.totals
            check: dict[str, Any] = {
                "okres_od": check_from.isoformat(),
                "okres_do": last_to.isoformat(),
                "godzin_w_statystykach": chk.hours,
                "licznik_pobrana_zbilansowana": round(chk.import_balanced, 2),
                "licznik_oddana_zbilansowana": round(chk.export_balanced, 2),
                "licznik_pobrana_surowa": round(chk.import_raw, 2),
                "licznik_oddana_surowa": round(chk.export_raw, 2),
                "rachunek_pobrana": last.get(S_IMPORT),
                "rachunek_oddana": last.get(S_EXPORT),
                "rachunek_magazyn": last[S_STORAGE],
            }
            if chk.hours:
                if last.get(S_IMPORT) is not None:
                    data["diff_import"] = last[S_IMPORT] - chk.import_balanced
                if last.get(S_EXPORT) is not None:
                    data["diff_export"] = last[S_EXPORT] - chk.export_balanced
                if prev:
                    expected = max(sim.saldo, 0.0)
                    check["licznik_magazyn_wyliczony"] = round(expected, 2)
                    check["wygaslo_w_okresie"] = round(sim.expired, 2)
                    check["magazyn_poprzedni"] = prev[S_STORAGE]
                    data["diff_storage"] = last[S_STORAGE] - expected
            data["check"] = check

        return data
