"""Inspect and download plain image URLs.

Anything that is not a YouTube link goes through here: a straight HTTP GET with
the shared Home Assistant session, streamed to disk so a huge file cannot be
buffered into memory.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import IMAGE_EXTENSIONS, IMAGE_SUFFIXES
from .filenames import (
    extension_from_url,
    sanitize_filename,
    stem_from_url,
    unique_path,
)

_LOGGER = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024
PREVIEW_TIMEOUT = aiohttp.ClientTimeout(total=15)
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=300, sock_read=60)


class ImageError(Exception):
    """Raised when the image could not be inspected or fetched."""


@dataclass
class ImagePreview:
    """What the card shows so you can check it is the right picture."""

    title: str
    content_type: str | None
    size: int | None
    url: str


@dataclass
class ImageResult:
    """Details about a completed image download."""

    path: str
    bytes_downloaded: int
    final_url: str


def looks_like_image_url(url: str) -> bool:
    """Return True when ``url`` is an http(s) URL that could be an image.

    Deliberately loose: the card treats every non-YouTube URL as an image
    candidate and the real check happens when the server answers.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def has_image_suffix(url: str) -> bool:
    """Return True when the URL path ends in a known image extension."""
    return extension_from_url(url) in IMAGE_SUFFIXES


async def async_preview_image(hass: HomeAssistant, url: str) -> ImagePreview:
    """Check ``url`` is reachable and looks like an image.

    The card renders the picture from the URL directly, so this only confirms
    the URL resolves and reports the type and size alongside it.
    """
    session = async_get_clientsession(hass)

    try:
        # Some hosts refuse HEAD, so fall back to a GET that is closed early.
        async with session.head(
            url, timeout=PREVIEW_TIMEOUT, allow_redirects=True
        ) as response:
            if response.status < 400:
                return _preview_from_response(url, response)

        async with session.get(
            url, timeout=PREVIEW_TIMEOUT, allow_redirects=True
        ) as response:
            response.raise_for_status()
            return _preview_from_response(url, response)
    except asyncio.TimeoutError as err:
        raise ImageError(f"Timed out reading {url}") from err
    except aiohttp.ClientError as err:
        raise ImageError(f"Could not read {url}: {err}") from err


def _preview_from_response(url: str, response: aiohttp.ClientResponse) -> ImagePreview:
    """Build a preview from response headers, rejecting non-images."""
    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
    content_type = content_type.lower() or None

    if not _is_image(content_type, url):
        raise ImageError(
            f"{url} is not an image (the server called it "
            f"'{content_type or 'nothing in particular'}')"
        )

    length = response.headers.get("Content-Length")
    return ImagePreview(
        title=stem_from_url(str(response.url) or url, fallback="image"),
        content_type=content_type,
        size=int(length) if length and length.isdigit() else None,
        url=str(response.url) or url,
    )


def _is_image(content_type: str | None, url: str) -> bool:
    """Accept anything the server calls an image, or any known extension.

    Static hosts that serve ``application/octet-stream`` are common enough that
    a recognisable extension is treated as good enough on its own.
    """
    if content_type and content_type in IMAGE_EXTENSIONS:
        return True
    if content_type and content_type.startswith("image/"):
        return True
    return has_image_suffix(url)


def _extension_for(url: str, content_type: str | None) -> str:
    """Pick the file extension, preferring the one already in the URL."""
    from_url = extension_from_url(url)
    if from_url in IMAGE_SUFFIXES:
        return ".jpg" if from_url == ".jpeg" else from_url
    return IMAGE_EXTENSIONS.get(content_type or "", ".jpg")


async def async_download_image(
    hass: HomeAssistant,
    url: str,
    destination_dir: str,
    *,
    stem: str,
    max_bytes: int,
    progress_callback: Callable[[float, int, int | None], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> ImageResult:
    """Stream ``url`` into ``destination_dir``.

    ``stem`` is the filename without extension; the extension comes from the URL
    or the served content type. Raises :class:`ImageError` on any failure.
    """
    session = async_get_clientsession(hass)

    try:
        async with session.get(
            url, timeout=DOWNLOAD_TIMEOUT, allow_redirects=True
        ) as response:
            response.raise_for_status()

            content_type = (
                (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            ) or None
            final_url = str(response.url) or url

            if not _is_image(content_type, final_url):
                raise ImageError(
                    f"{url} is not an image (the server called it "
                    f"'{content_type or 'nothing in particular'}')"
                )

            total = response.headers.get("Content-Length")
            total = int(total) if total and total.isdigit() else None
            if total and total > max_bytes:
                raise ImageError(
                    f"The image is {int(total / (1024 * 1024))} MB which exceeds the "
                    f"{int(max_bytes / (1024 * 1024))} MB limit"
                )

            extension = _extension_for(final_url, content_type)
            path = await hass.async_add_executor_job(
                _reserve_path, destination_dir, sanitize_filename(stem, fallback="image"), extension
            )

            downloaded = await _stream_to_file(
                hass, response, path, max_bytes, total, progress_callback, cancel_event
            )
    except ImageError:
        raise
    except asyncio.TimeoutError as err:
        raise ImageError(f"Timed out downloading {url}") from err
    except aiohttp.ClientError as err:
        raise ImageError(f"Could not download {url}: {err}") from err

    return ImageResult(path=path, bytes_downloaded=downloaded, final_url=final_url)


def _reserve_path(destination_dir: str, stem: str, extension: str) -> str:
    """Create the folder and claim a free filename (blocking; executor only)."""
    os.makedirs(destination_dir, exist_ok=True)
    return unique_path(destination_dir, stem, extension)


async def _stream_to_file(
    hass: HomeAssistant,
    response: aiohttp.ClientResponse,
    path: str,
    max_bytes: int,
    total: int | None,
    progress_callback: Callable[[float, int, int | None], None] | None,
    cancel_event: threading.Event | None,
) -> int:
    """Write the response body to ``path``, cleaning up if it goes wrong."""
    downloaded = 0

    def _open():
        return open(path, "wb")  # noqa: SIM115 - closed in the finally below

    handle = await hass.async_add_executor_job(_open)
    try:
        async for chunk in response.content.iter_chunked(CHUNK_SIZE):
            if cancel_event is not None and cancel_event.is_set():
                raise ImageError("Cancelled")

            downloaded += len(chunk)
            if downloaded > max_bytes:
                raise ImageError(
                    f"Download exceeded the {int(max_bytes / (1024 * 1024))} MB limit"
                )

            await hass.async_add_executor_job(handle.write, chunk)

            if progress_callback is not None:
                fraction = (downloaded / total) if total else 0.0
                progress_callback(min(fraction, 1.0), downloaded, total)
    except BaseException:
        await hass.async_add_executor_job(handle.close)
        await hass.async_add_executor_job(_remove_quietly, path)
        raise
    else:
        await hass.async_add_executor_job(handle.close)

    return downloaded


def _remove_quietly(path: str) -> None:
    """Delete a partial download, ignoring anything that goes wrong."""
    try:
        os.remove(path)
    except OSError as err:
        _LOGGER.debug("Could not remove partial download %s: %s", path, err)
