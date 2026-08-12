"""Config and options flow for YouTube Download."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_MAX_DOWNLOAD_MB,
    CONF_MAX_HISTORY,
    CONF_PREFERRED_FOLDER,
    DEFAULT_MAX_DOWNLOAD_MB,
    DEFAULT_MAX_HISTORY,
    DEFAULT_PREFERRED_FOLDER,
    DOMAIN,
)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    """Return the settings form, pre-filled from ``defaults``."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_PREFERRED_FOLDER,
                default=defaults.get(CONF_PREFERRED_FOLDER, DEFAULT_PREFERRED_FOLDER),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_MAX_DOWNLOAD_MB,
                default=defaults.get(CONF_MAX_DOWNLOAD_MB, DEFAULT_MAX_DOWNLOAD_MB),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=10000, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_MAX_HISTORY,
                default=defaults.get(CONF_MAX_HISTORY, DEFAULT_MAX_HISTORY),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=100, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalise the form values (the number selector hands back floats)."""
    return {
        CONF_PREFERRED_FOLDER: (
            user_input.get(CONF_PREFERRED_FOLDER) or ""
        ).strip(),
        CONF_MAX_DOWNLOAD_MB: int(
            user_input.get(CONF_MAX_DOWNLOAD_MB, DEFAULT_MAX_DOWNLOAD_MB)
        ),
        CONF_MAX_HISTORY: int(user_input.get(CONF_MAX_HISTORY, DEFAULT_MAX_HISTORY)),
    }


class YouTubeDownloadConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set the integration up from the UI."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step. One entry is enough."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(
                title="YouTube Download", data=_clean(user_input)
            )

        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return YouTubeDownloadOptionsFlow()


class YouTubeDownloadOptionsFlow(OptionsFlow):
    """Change the settings after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and save the settings form."""
        if user_input is not None:
            return self.async_create_entry(title="", data=_clean(user_input))

        defaults = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(defaults))
