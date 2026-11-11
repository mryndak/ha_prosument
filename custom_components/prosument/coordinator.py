"""Koordynator: liczy stan magazynu ze statystyk godzinowych rejestratora."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.debounce import Debouncer
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
    LIVE_COOLDOWN_SECONDS,
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
from .live import LiveHour

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
        # Szeregi godzinowe pobrane z rejestratora przy ostatnim odświeżeniu.
        self._cache: dict[str, Series | None] | None = None
        conf = self.conf
        self.live = LiveHour(
            hass, conf[CONF_IMPORT], conf[CONF_EXPORT], self._live_changed
        )
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=LIVE_COOLDOWN_SECONDS,
            immediate=True,
            function=self._publish_live,
        )

    async def async_start_live(self) -> None:
        await self.live.async_start()

    @callback
    def async_stop_live(self) -> None:
        self.live.async_stop()
        self._debouncer.async_shutdown()

    @callback
    def _live_changed(self) -> None:
        """Licznik się zmienił: przelicz, ale nie częściej niż co kilka sekund."""
        self._debouncer.async_schedule_call()

    @callback
    def _publish_live(self) -> None:
        """Przelicz sensory z danych w pamięci, bez pytania rejestratora.

        Celowo bez async_set_updated_data: ono przesuwa termin kolejnego
        odświeżenia statystyk, więc przy częstych zmianach nigdy by nie nastąpiło.
        """
        if self._cache is None:
            return
        try:
            self.data = self._compute()
        except Exception:  # noqa: BLE001 - błąd na żywo nie może zatrzymać integracji
            _LOGGER.exception("Błąd przeliczania danych na żywo")
            return
        self.async_update_listeners()

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

    def _plan(self) -> dict[str, Any]:
        """Rachunki i granice okresów wynikające z konfiguracji."""
        conf = self.conf
        settlements = sorted(conf.get(CONF_SETTLEMENTS, []), key=lambda s: s[S_TO])
        pairs = [(date.fromisoformat(s[S_TO]), s[S_STORAGE]) for s in settlements]
        last = settlements[-1]
        prev = settlements[-2] if len(settlements) > 1 else None
        last_to = pairs[-1][0]
        if last.get(S_FROM):
            check_from: date | None = date.fromisoformat(last[S_FROM])
        elif prev:
            check_from = pairs[-2][0] + timedelta(days=1)
        else:
            check_from = None
        return {
            "pairs": pairs,
            "last": last,
            "prev": prev,
            "last_to": last_to,
            "start": _day_start(last_to + timedelta(days=1)),
            "check_from": check_from,
            "window_days": int(conf.get(CONF_FORECAST_DAYS, DEFAULT_FORECAST_DAYS)),
        }

    async def _async_update_data(self) -> dict[str, Any]:
        """Pobierz szeregi godzinowe z rejestratora i przelicz sensory."""
        plan = self._plan()
        window_start = dt_util.utcnow().replace(
            minute=0, second=0, microsecond=0
        ) - timedelta(days=plan["window_days"])
        cache: dict[str, Series | None] = {
            "cur": await self._series(plan["start"], None),
            "win": await self._series(window_start, None),
            "chk": None,
        }
        if plan["check_from"] is not None:
            cache["chk"] = await self._series(
                _day_start(plan["check_from"]), plan["start"]
            )
        self._cache = cache
        cur = cache["cur"]
        assert cur is not None
        self.live.prune(set(cur[0]) | set(cur[1]))
        return self._compute()

    def _compute(self) -> dict[str, Any]:
        """Policz wszystkie wartości z szeregów w pamięci i bieżącej godziny."""
        assert self._cache is not None
        conf = self.conf
        plan = self._plan()
        factor: float = conf.get(CONF_FACTOR, DEFAULT_FACTOR)
        price: float = conf.get(CONF_PRICE, DEFAULT_PRICE)
        months = int(conf.get(CONF_EXPIRY_MONTHS, DEFAULT_EXPIRY_MONTHS))
        pairs = plan["pairs"]
        last = plan["last"]
        prev = plan["prev"]
        last_to = plan["last_to"]
        start = plan["start"]

        # --- Okres bieżący: od dnia po ostatnim rozliczeniu do teraz ---
        cached = self._cache["cur"]
        assert cached is not None
        cur_imp, cur_exp = dict(cached[0]), dict(cached[1])
        # Godziny, których statystyk jeszcze nie ma: bieżąca i świeżo zamknięte.
        for hour_ts, imported, exported in self.live.hours():
            if (
                hour_ts >= start.timestamp()
                and hour_ts not in cur_imp
                and hour_ts not in cur_exp
            ):
                cur_imp[hour_ts] = imported
                cur_exp[hour_ts] = exported
        res = self._simulate(
            derive_batches(pairs, months),
            (cur_imp, cur_exp),
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
            "hour_balance": None,
            "hour": {},
        }

        # --- Bieżąca godzina na żywo ---
        live_hour = self.live.current()
        if live_hour is not None:
            hour_ts, imported, exported = live_hour
            data["hour_balance"] = exported - imported
            data["hour"] = {
                "godzina_od": dt_util.as_local(
                    dt_util.utc_from_timestamp(hour_ts)
                ).isoformat(),
                "pobrane_w_tej_godzinie": round(imported, 3),
                "oddane_w_tej_godzinie": round(exported, 3),
                "niepelna_godzina": self.live.partial,
            }

        # --- Prognoza: średni ubytek magazynu z ostatnich dni ---
        window_days = plan["window_days"]
        window = self._cache["win"]
        assert window is not None
        win = balance(window[0], window[1], conf.get(CONF_HOURLY, True))
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
        check_from = plan["check_from"]
        check_series = self._cache["chk"]
        if check_from is not None and check_series is not None:
            sim = self._simulate(
                derive_batches(pairs[:-1], months),
                check_series,
                start,
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
