"""Sensory integracji Prosument."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ProsumentCoordinator


@dataclass(frozen=True, kw_only=True)
class ProsumentSensorDescription(SensorEntityDescription):
    """Opis sensora z funkcją zwracającą dodatkowe atrybuty."""

    attrs: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _main_attrs(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "liczone_od": d["period_start"],
        "magazyn_z_rachunku": d["start_storage"],
        "wspolczynnik": d["factor"],
        "cena_za_kwh": d["price"],
        "waznosc_miesiace": d["expiry_months"],
        "wygaslo_od_rozliczenia": round(d["expired_kwh"], 2),
        "partie": d["batches"],
        "godzin_w_statystykach": d["hours"],
    }


def _check_attrs(d: dict[str, Any]) -> dict[str, Any]:
    return d["check"]


def _storage(key: str, name: str, icon: str, **kw: Any) -> ProsumentSensorDescription:
    return ProsumentSensorDescription(
        key=key,
        name=name,
        icon=icon,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        **kw,
    )


def _energy(key: str, name: str, icon: str) -> ProsumentSensorDescription:
    return ProsumentSensorDescription(
        key=key,
        name=name,
        icon=icon,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
    )


SENSORS: tuple[ProsumentSensorDescription, ...] = (
    _storage(
        "storage_left",
        "Magazyn – energia pozostała",
        "mdi:home-battery",
        attrs=_main_attrs,
    ),
    _storage("to_pay_kwh", "Energia do zapłaty", "mdi:transmission-tower-import"),
    ProsumentSensorDescription(
        key="cost",
        name="Szacowany koszt",
        icon="mdi:cash",
        native_unit_of_measurement="PLN",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
    ),
    _storage("saldo", "Saldo magazynu", "mdi:scale-balance"),
    ProsumentSensorDescription(
        key="next_expiry_date",
        name="Najbliższe wygaśnięcie",
        icon="mdi:calendar-alert",
        device_class=SensorDeviceClass.DATE,
    ),
    _storage("next_expiry_kwh", "Energia wygasająca najbliżej", "mdi:timer-sand"),
    _storage("expired_kwh", "Energia wygasła od rozliczenia", "mdi:delete-clock"),
    _energy("import_balanced", "Pobrana od rozliczenia", "mdi:transmission-tower-import"),
    _energy("export_balanced", "Oddana od rozliczenia", "mdi:transmission-tower-export"),
    _energy("import_raw", "Pobrana od rozliczenia (licznik)", "mdi:counter"),
    _energy("export_raw", "Oddana od rozliczenia (licznik)", "mdi:counter"),
    _storage(
        "diff_storage",
        "Rachunek – różnica magazynu",
        "mdi:file-compare",
        attrs=_check_attrs,
    ),
    _storage(
        "diff_import",
        "Rachunek – różnica energii pobranej",
        "mdi:file-compare",
        attrs=_check_attrs,
    ),
    _storage(
        "diff_export",
        "Rachunek – różnica energii oddanej",
        "mdi:file-compare",
        attrs=_check_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ProsumentCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(ProsumentSensor(coordinator, entry, desc) for desc in SENSORS)


class ProsumentSensor(CoordinatorEntity[ProsumentCoordinator], SensorEntity):
    """Pojedyncza wartość wyliczona przez koordynator."""

    entity_description: ProsumentSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ProsumentCoordinator,
        entry: ConfigEntry,
        description: ProsumentSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Prosument",
            model="Net-metering",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> float | date | None:
        value = self.coordinator.data.get(self.entity_description.key)
        if value is None or isinstance(value, date):
            return value
        return round(value, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs is None:
            return None
        return self.entity_description.attrs(self.coordinator.data)
