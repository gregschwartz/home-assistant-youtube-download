"""YouTube Download - paste a YouTube or image URL, file it in your media folders."""
from __future__ import annotations

import logging
import os
from typing import Any

import voluptuous as vol
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_PREFERRED_FOLDER,
    DOMAIN,
    FRONTEND_FILENAME,
    FRONTEND_URL,
    SERVICE_CANCEL_JOB,
    SERVICE_DOWNLOAD_URL,
    SERVICE_LIST_MEDIA_FOLDERS,
)
from .manager import DownloadError, DownloadManager
from .media_folders import async_list_media_folders, preferred_media_folder
from .websocket_api import async_register as async_register_websocket_api

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# hass.data[DOMAIN] maps entry_id -> DownloadManager and nothing else.
FRONTEND_FLAG = f"{DOMAIN}_frontend_registered"

DOWNLOAD_URL_SCHEMA = vol.Schema(
    {
        vol.Required("url"): cv.string,
        vol.Required("folder"): cv.string,
        vol.Optional("filename"): cv.string,
    }
)

CANCEL_JOB_SCHEMA = vol.Schema({vol.Required("job_id"): cv.string})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration (config entries only)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry."""
    manager = DownloadManager(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = manager

    await _async_register_frontend(hass)
    async_register_websocket_api(hass)
    _async_register_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear a config entry down."""
    manager: DownloadManager | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if manager is not None:
        await manager.async_shutdown()

    if not hass.data.get(DOMAIN):
        for service in (
            SERVICE_DOWNLOAD_URL,
            SERVICE_CANCEL_JOB,
            SERVICE_LIST_MEDIA_FOLDERS,
        ):
            hass.services.async_remove(DOMAIN, service)

    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the Lovelace card and add it as a dashboard resource."""
    if hass.data.get(FRONTEND_FLAG):
        return
    hass.data[FRONTEND_FLAG] = True

    path = os.path.join(os.path.dirname(__file__), "frontend", FRONTEND_FILENAME)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL, path, cache_headers=False)]
    )

    # Only storage-mode dashboards can be edited from here; a YAML dashboard
    # needs the resource added by hand, which the README explains.
    try:
        resources = hass.data["lovelace"].resources
    except (KeyError, AttributeError):
        _LOGGER.debug("Lovelace resources unavailable; add the card resource manually")
        return

    try:
        if not resources.loaded:
            await resources.async_load()
            resources.loaded = True

        if any(item["url"].startswith(FRONTEND_URL) for item in resources.async_items()):
            return

        await resources.async_create_item({"res_type": "module", "url": FRONTEND_URL})
    except Exception as err:  # noqa: BLE001 - a YAML dashboard raises here
        _LOGGER.debug("Could not register the card resource automatically: %s", err)


def _get_manager(hass: HomeAssistant) -> DownloadManager:
    """Return the single manager, or raise a user-visible error."""
    managers = list(hass.data.get(DOMAIN, {}).values())
    if not managers:
        raise ServiceValidationError("YouTube Download is not set up")
    return managers[0]


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the services, once, regardless of entry count."""
    if hass.services.has_service(DOMAIN, SERVICE_DOWNLOAD_URL):
        return

    async def _download_url(call: ServiceCall) -> dict[str, Any]:
        """Download a YouTube or image URL into a media folder."""
        manager = _get_manager(hass)
        try:
            job = await manager.async_start_download(
                call.data["url"],
                folder=call.data["folder"],
                filename=call.data.get("filename"),
            )
        except DownloadError as err:
            raise ServiceValidationError(str(err)) from err
        return {"job_id": job.id}

    async def _cancel_job(call: ServiceCall) -> dict[str, Any]:
        """Cancel a running download."""
        manager = _get_manager(hass)
        return {"cancelled": await manager.async_cancel_job(call.data["job_id"])}

    async def _list_media_folders(call: ServiceCall) -> dict[str, Any]:
        """List the folders a download can be filed into."""
        manager = _get_manager(hass)
        folders = await async_list_media_folders(hass)
        return {
            "folders": folders,
            "preferred": preferred_media_folder(
                folders, manager.options[CONF_PREFERRED_FOLDER]
            ),
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_DOWNLOAD_URL,
        _download_url,
        schema=DOWNLOAD_URL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CANCEL_JOB,
        _cancel_job,
        schema=CANCEL_JOB_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_MEDIA_FOLDERS,
        _list_media_folders,
        supports_response=SupportsResponse.ONLY,
    )
