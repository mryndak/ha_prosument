"""Konfiguracja integracji Prosument z poziomu interfejsu."""

from __future__ import annotations

from datetime import date
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    DateSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_EXPIRY_MONTHS,
    CONF_EXPORT,
    CONF_FACTOR,
    CONF_HOURLY,
    CONF_IMPORT,
    CONF_PRICE,
    CONF_SETTLEMENTS,
    DEFAULT_EXPIRY_MONTHS,
    DEFAULT_FACTOR,
    DEFAULT_PRICE,
    DOMAIN,
    MAX_SETTLEMENTS,
    S_EXPORT,
    S_FROM,
    S_IMPORT,
    S_STORAGE,
    S_TO,
)

_ENERGY_ENTITY = EntitySelector(
    EntitySelectorConfig(domain="sensor", device_class="energy")
)
_KWH = NumberSelector(
    NumberSelectorConfig(
        min=0,
        max=1_000_000,
        step=0.01,
        mode=NumberSelectorMode.BOX,
        unit_of_measurement="kWh",
    )
)


def _settings_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_IMPORT, default=defaults.get(CONF_IMPORT, vol.UNDEFINED)
            ): _ENERGY_ENTITY,
            vol.Required(
                CONF_EXPORT, default=defaults.get(CONF_EXPORT, vol.UNDEFINED)
            ): _ENERGY_ENTITY,
            vol.Required(
                CONF_FACTOR, default=defaults.get(CONF_FACTOR, DEFAULT_FACTOR)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0.1, max=1, step=0.01, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_PRICE, default=defaults.get(CONF_PRICE, DEFAULT_PRICE)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=10,
                    step=0.01,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="zł/kWh",
                )
            ),
            vol.Required(
                CONF_EXPIRY_MONTHS,
                default=defaults.get(CONF_EXPIRY_MONTHS, DEFAULT_EXPIRY_MONTHS),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=60,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="mies.",
                )
            ),
            vol.Required(
                CONF_HOURLY, default=defaults.get(CONF_HOURLY, True)
            ): BooleanSelector(),
        }
    )


def _settlement_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(S_FROM): DateSelector(),
            vol.Required(S_TO, default=date.today().isoformat()): DateSelector(),
            vol.Required(S_STORAGE, default=0): _KWH,
            vol.Optional(S_IMPORT): _KWH,
            vol.Optional(S_EXPORT): _KWH,
        }
    )


def _settings_errors(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    """Sprawdź, czy wybrane encje nadają się na liczniki importu i eksportu."""
    if user_input[CONF_IMPORT] == user_input[CONF_EXPORT]:
        return {"base": "same_entity"}
    for key in (CONF_IMPORT, CONF_EXPORT):
        state = hass.states.get(user_input[key])
        # Bez state_class rejestrator nie tworzy statystyk długoterminowych.
        if state is not None and state.attributes.get("state_class") not in (
            "total",
            "total_increasing",
        ):
            return {key: "no_statistics"}
    return {}


def _settlement_errors(user_input: dict[str, Any]) -> dict[str, str]:
    if user_input.get(S_FROM) and user_input[S_FROM] > user_input[S_TO]:
        return {"base": "dates"}
    return {}


def _merge_settlement(
    settlements: list[dict[str, Any]], new: dict[str, Any]
) -> list[dict[str, Any]]:
    """Dodaj rozliczenie; rachunek z tą samą datą końca zastępuje stary."""
    merged = [s for s in settlements if s[S_TO] != new[S_TO]] + [new]
    merged.sort(key=lambda s: s[S_TO])
    return merged[-MAX_SETTLEMENTS:]


class ProsumentConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pierwsza konfiguracja: ustawienia, potem dane z ostatniego rachunku."""

    VERSION = 1

    def __init__(self) -> None:
        self._settings: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _settings_errors(self.hass, user_input)
            if not errors:
                self._settings = user_input
                return await self.async_step_settlement()
        return self.async_show_form(
            step_id="user",
            data_schema=_settings_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_settlement(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _settlement_errors(user_input)
            if not errors:
                return self.async_create_entry(
                    title="Prosument",
                    data={},
                    options={**self._settings, CONF_SETTLEMENTS: [user_input]},
                )
        return self.async_show_form(
            step_id="settlement", data_schema=_settlement_schema(), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ProsumentOptionsFlow()


class ProsumentOptionsFlow(OptionsFlow):
    """Nowy rachunek, zmiana ustawień, cofnięcie ostatniego rachunku."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_settlement", "settings", "remove_last"],
        )

    async def async_step_add_settlement(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _settlement_errors(user_input)
            if not errors:
                options = dict(self.config_entry.options)
                options[CONF_SETTLEMENTS] = _merge_settlement(
                    options.get(CONF_SETTLEMENTS, []), user_input
                )
                return self.async_create_entry(data=options)
        return self.async_show_form(
            step_id="add_settlement",
            data_schema=_settlement_schema(),
            errors=errors,
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _settings_errors(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    data={**self.config_entry.options, **user_input}
                )
        return self.async_show_form(
            step_id="settings",
            data_schema=_settings_schema(user_input or dict(self.config_entry.options)),
            errors=errors,
        )

    async def async_step_remove_last(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        settlements = sorted(
            self.config_entry.options.get(CONF_SETTLEMENTS, []),
            key=lambda s: s[S_TO],
        )
        if len(settlements) < 2:
            return self.async_abort(reason="only_one_settlement")
        if user_input is not None:
            options = dict(self.config_entry.options)
            options[CONF_SETTLEMENTS] = settlements[:-1]
            return self.async_create_entry(data=options)
        last = settlements[-1]
        return self.async_show_form(
            step_id="remove_last",
            data_schema=vol.Schema({}),
            description_placeholders={
                "date": last[S_TO],
                "storage": str(last[S_STORAGE]),
            },
        )
