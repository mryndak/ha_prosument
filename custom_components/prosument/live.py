"""Śledzenie bieżącej godziny na żywo, ze stanów liczników."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfEnergy,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

_LOGGER = logging.getLogger(__name__)

# (początek godziny jako znacznik czasu, pobór kWh, oddanie kWh)
LiveHourData = tuple[float, float, float]

MAX_PENDING = 3


def _hour_start(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


class LiveHour:
    """Liczy pobór i oddanie w bieżącej godzinie z bieżących stanów liczników.

    Statystyki godzinowe pojawiają się w rejestratorze dopiero po zamknięciu
    godziny. Ta klasa wypełnia tę lukę: podaje bieżącą godzinę oraz godziny
    już zamknięte, których statystyk jeszcze nie ma.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        import_id: str,
        export_id: str,
        on_change: Callable[[], None],
    ) -> None:
        self._hass = hass
        self._import_id = import_id
        self._export_id = export_id
        self._on_change = on_change
        self._base: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._hour_ts = _hour_start(dt_util.utcnow()).timestamp()
        self._pending: list[LiveHourData] = []
        self._unsubs: list[Callable[[], None]] = []
        # True, gdy nie znamy stanu licznika z początku godziny
        self.partial = False

    def _read(self, entity_id: str) -> float | None:
        """Bieżący stan licznika w kWh albo None, gdy jest niedostępny."""
        state = self._hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        try:
            return EnergyConverter.convert(
                float(state.state), unit, UnitOfEnergy.KILO_WATT_HOUR
            )
        except Exception:  # noqa: BLE001 - zły stan lub jednostka: pomiń odczyt
            return None

    async def _stat_baseline(self, hour: datetime) -> dict[str, float]:
        """Stan liczników na początku godziny, ze statystyk poprzedniej godziny."""
        ids = {self._import_id, self._export_id}
        try:
            stats = await get_instance(self._hass).async_add_executor_job(
                statistics_during_period,
                self._hass,
                hour - timedelta(hours=1),
                hour,
                ids,
                "hour",
                {"energy": UnitOfEnergy.KILO_WATT_HOUR},
                {"state"},
            )
        except Exception:  # noqa: BLE001 - brak statystyk nie blokuje startu
            _LOGGER.debug("Brak statystyk do wyznaczenia początku godziny", exc_info=True)
            return {}
        result: dict[str, float] = {}
        for entity_id in ids:
            rows = stats.get(entity_id) or []
            if rows and rows[-1].get("state") is not None:
                result[entity_id] = rows[-1]["state"]
        return result

    async def async_start(self) -> None:
        """Ustal stan z początku godziny i zacznij nasłuchiwać zmian."""
        hour = _hour_start(dt_util.utcnow())
        self._hour_ts = hour.timestamp()
        baseline = await self._stat_baseline(hour)
        for entity_id in (self._import_id, self._export_id):
            value = self._read(entity_id)
            if value is not None:
                self._last[entity_id] = value
            base = baseline.get(entity_id)
            if base is None or (value is not None and value < base):
                # brak statystyk albo licznik się wyzerował: liczymy od teraz
                self.partial = True
                base = value
            if base is not None:
                self._base[entity_id] = base
        self._unsubs.append(
            async_track_state_change_event(
                self._hass, [self._import_id, self._export_id], self._on_state
            )
        )
        self._unsubs.append(
            async_track_time_change(self._hass, self._on_hour, minute=0, second=0)
        )

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def _on_state(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        value = self._read(entity_id)
        if value is None:
            return
        base = self._base.get(entity_id)
        if base is None or value < base:
            # pierwszy odczyt w tej godzinie albo wyzerowanie licznika
            self._base[entity_id] = value
            self.partial = True
        self._last[entity_id] = value
        self._on_change()

    @callback
    def _on_hour(self, now: datetime) -> None:
        """Zamknij godzinę: zapamiętaj ją do czasu pojawienia się statystyk."""
        finished = self.current()
        if finished is not None:
            self._pending = [*self._pending, finished][-MAX_PENDING:]
        self._base = dict(self._last)
        self._hour_ts = _hour_start(dt_util.utcnow()).timestamp()
        self.partial = False
        self._on_change()

    def _delta(self, entity_id: str) -> float | None:
        if entity_id not in self._base or entity_id not in self._last:
            return None
        return max(self._last[entity_id] - self._base[entity_id], 0.0)

    def current(self) -> LiveHourData | None:
        """Bieżąca godzina albo None, gdy brakuje odczytu któregoś licznika."""
        imported = self._delta(self._import_id)
        exported = self._delta(self._export_id)
        if imported is None or exported is None:
            return None
        return (self._hour_ts, imported, exported)

    def hours(self) -> list[LiveHourData]:
        """Zamknięte godziny bez statystyk oraz godzina bieżąca."""
        current = self.current()
        return [*self._pending, current] if current is not None else list(self._pending)

    def prune(self, known: set[float]) -> None:
        """Usuń zamknięte godziny, dla których statystyki już istnieją."""
        self._pending = [hour for hour in self._pending if hour[0] not in known]
