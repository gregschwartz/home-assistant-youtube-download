"""WebSocket API used by the Lovelace card.

Going over the websocket connection means the card needs no long-lived access
token and gets live progress pushes instead of polling.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import CONF_PREFERRED_FOLDER, DOMAIN
from .manager import DownloadError, DownloadJob, DownloadManager
from .media_folders import async_list_media_folders, preferred_media_folder

_LOGGER = logging.getLogger(__name__)

NOT_LOADED = "YouTube Download is not set up"


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register every websocket command."""
    websocket_api.async_register_command(hass, ws_folders)
    websocket_api.async_register_command(hass, ws_preview)
    websocket_api.async_register_command(hass, ws_download)
    websocket_api.async_register_command(hass, ws_cancel)
    websocket_api.async_register_command(hass, ws_clear_finished)
    websocket_api.async_register_command(hass, ws_subscribe)


@callback
def _get_manager(hass: HomeAssistant) -> DownloadManager | None:
    """Return the single manager instance, if the integration is loaded."""
    managers = list(hass.data.get(DOMAIN, {}).values())
    return managers[0] if managers else None


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/folders"})
@websocket_api.async_response
async def ws_folders(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """List the media folders a download can be filed into."""
    manager = _get_manager(hass)
    if manager is None:
        connection.send_error(msg["id"], "not_loaded", NOT_LOADED)
        return

    folders = await async_list_media_folders(hass)
    connection.send_result(
        msg["id"],
        {
            "folders": folders,
            # What a YouTube URL will pre-select; images pre-select nothing.
            "preferred": preferred_media_folder(
                folders, manager.options[CONF_PREFERRED_FOLDER]
            ),
        },
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/preview", vol.Required("url"): str}
)
@websocket_api.async_response
async def ws_preview(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Describe a URL so it can be checked before downloading."""
    manager = _get_manager(hass)
    if manager is None:
        connection.send_error(msg["id"], "not_loaded", NOT_LOADED)
        return

    try:
        preview = await manager.async_preview(msg["url"])
    except DownloadError as err:
        connection.send_error(msg["id"], "invalid_url", str(err))
        return

    connection.send_result(msg["id"], preview)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/download",
        vol.Required("url"): str,
        vol.Required("folder"): str,
        vol.Optional("filename"): vol.Any(str, None),
    }
)
@websocket_api.async_response
async def ws_download(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Start a download and return its job id."""
    manager = _get_manager(hass)
    if manager is None:
        connection.send_error(msg["id"], "not_loaded", NOT_LOADED)
        return

    try:
        job = await manager.async_start_download(
            msg["url"], folder=msg["folder"], filename=msg.get("filename")
        )
    except DownloadError as err:
        connection.send_error(msg["id"], "invalid_request", str(err))
        return

    connection.send_result(msg["id"], {"job_id": job.id})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/cancel", vol.Required("job_id"): str}
)
@websocket_api.async_response
async def ws_cancel(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Cancel a running download."""
    manager = _get_manager(hass)
    if manager is None:
        connection.send_error(msg["id"], "not_loaded", NOT_LOADED)
        return

    connection.send_result(
        msg["id"], {"cancelled": await manager.async_cancel_job(msg["job_id"])}
    )


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/clear_finished"})
@callback
def ws_clear_finished(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Remove finished downloads from the list."""
    manager = _get_manager(hass)
    if manager is None:
        connection.send_error(msg["id"], "not_loaded", NOT_LOADED)
        return

    connection.send_result(msg["id"], {"removed": manager.clear_finished()})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Send the job list now and on every change."""
    manager = _get_manager(hass)
    if manager is None:
        connection.send_error(msg["id"], "not_loaded", NOT_LOADED)
        return

    @callback
    def _on_change(job: DownloadJob | None) -> None:
        if job is not None:
            connection.send_message(
                websocket_api.event_message(
                    msg["id"], {"type": "job", "job": job.to_dict()}
                )
            )
        else:
            connection.send_message(
                websocket_api.event_message(
                    msg["id"], {"type": "jobs", "jobs": manager.jobs_as_dicts()}
                )
            )

    connection.subscriptions[msg["id"]] = manager.async_add_listener(_on_change)
    connection.send_result(msg["id"])
    _on_change(None)
