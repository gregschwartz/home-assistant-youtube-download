"""Diagnostic sensor: which yt-dlp version the integration is running."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .manager import DownloadJob, DownloadManager


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add the version sensor for a config entry."""
    async_add_entities([YtDlpVersionSensor(hass.data[DOMAIN][entry.entry_id])])


class YtDlpVersionSensor(SensorEntity):
    """Shows the installed yt-dlp version; refreshes after every upgrade."""

    _attr_has_entity_name = True
    _attr_name = "yt-dlp version"
    _attr_icon = "mdi:update"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, manager: DownloadManager) -> None:
        """Bind to the manager that owns the version."""
        self._manager = manager
        self._attr_unique_id = f"{manager.entry.entry_id}_yt_dlp_version"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, manager.entry.entry_id)}, name="YouTube Download"
        )

    @property
    def native_value(self) -> str | None:
        """Return the installed version."""
        return self._manager.ytdlp_version

    async def async_added_to_hass(self) -> None:
        """Re-render whenever the manager reports a change."""

        def _changed(_job: DownloadJob | None) -> None:
            self.async_write_ha_state()

        self.async_on_remove(self._manager.async_add_listener(_changed))
