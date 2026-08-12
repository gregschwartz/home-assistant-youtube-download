"""Inspect and download YouTube URLs with yt-dlp.

YouTube never serves a plain media file, so it takes a different path from an
image URL: yt-dlp resolves the best audio stream and ffmpeg (shipped with Home
Assistant) turns it into the MP3 that gets filed.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from homeassistant.core import HomeAssistant

from .const import YOUTUBE_HOSTS
from .filenames import sanitize_filename, unique_path

_LOGGER = logging.getLogger(__name__)

AUDIO_FORMAT = "mp3"
AUDIO_EXTENSION = f".{AUDIO_FORMAT}"
AUDIO_QUALITY = "192"


class YouTubeError(Exception):
    """Raised when the video could not be inspected or fetched."""


@dataclass
class YouTubePreview:
    """What the card shows so you can check it is the right video."""

    title: str
    uploader: str | None
    duration: float | None
    thumbnail: str | None
    url: str


@dataclass
class YouTubeResult:
    """Details about a completed YouTube download."""

    path: str
    title: str
    duration: float | None
    bytes_downloaded: int
    final_url: str


def is_youtube_url(url: str) -> bool:
    """Return True when ``url`` points at YouTube."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in YOUTUBE_HOSTS


async def async_preview_youtube(hass: HomeAssistant, url: str) -> YouTubePreview:
    """Fetch title, uploader, duration and thumbnail without downloading."""
    return await hass.async_add_executor_job(_preview, url)


async def async_download_youtube(
    hass: HomeAssistant,
    url: str,
    destination_dir: str,
    *,
    stem: str,
    max_bytes: int,
    progress_callback: Callable[[float, int, int | None], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> YouTubeResult:
    """Download ``url`` as an MP3 into ``destination_dir``.

    ``stem`` is the filename without extension. Raises :class:`YouTubeError` on
    any failure, including the download being cancelled.
    """
    return await hass.async_add_executor_job(
        _download,
        url,
        destination_dir,
        stem,
        max_bytes,
        progress_callback,
        cancel_event,
    )


def _ydl():
    """Import yt-dlp, turning a missing dependency into a readable error."""
    try:
        import yt_dlp
    except ImportError as err:  # pragma: no cover - dependency is in the manifest
        raise YouTubeError(
            "yt-dlp is not installed, so YouTube URLs cannot be downloaded. "
            "Restart Home Assistant to let it install the integration's "
            "requirements."
        ) from err
    return yt_dlp


def _single_entry(info: dict[str, Any], url: str) -> dict[str, Any]:
    """Return the video itself, unwrapping a playlist that slipped through."""
    if info is None:
        raise YouTubeError(f"yt-dlp returned nothing for {url}")

    if "entries" in info:
        entries = [entry for entry in info["entries"] if entry]
        if not entries:
            raise YouTubeError(f"No downloadable video at {url}")
        return entries[0]

    return info


def _preview(url: str) -> YouTubePreview:
    """Pull metadata only (blocking; executor only)."""
    yt_dlp = _ydl()

    options = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
        "logger": _LOGGER,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = _single_entry(ydl.extract_info(url, download=False), url)
    except YouTubeError:
        raise
    except Exception as err:  # noqa: BLE001 - yt-dlp raises a wide variety
        raise YouTubeError(f"Could not read {url}: {err}") from err

    duration = info.get("duration")
    return YouTubePreview(
        title=info.get("title") or "Untitled",
        uploader=info.get("uploader") or info.get("channel"),
        duration=float(duration) if duration else None,
        thumbnail=_best_thumbnail(info),
        url=info.get("webpage_url") or url,
    )


def _best_thumbnail(info: dict[str, Any]) -> str | None:
    """Return the widest thumbnail yt-dlp knows about."""
    if info.get("thumbnail"):
        return info["thumbnail"]

    thumbnails = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    if not thumbnails:
        return None

    return max(thumbnails, key=lambda t: t.get("width") or 0)["url"]


def _download(
    url: str,
    destination_dir: str,
    stem: str,
    max_bytes: int,
    progress_callback: Callable[[float, int, int | None], None] | None,
    cancel_event: threading.Event | None,
) -> YouTubeResult:
    """Run yt-dlp (blocking; executor only)."""
    yt_dlp = _ydl()

    os.makedirs(destination_dir, exist_ok=True)

    # Reserve the final name up front so a second download of the same video
    # lands beside the first instead of on top of it. yt-dlp appends the source
    # extension and the postprocessor swaps in .mp3, so the template drops it.
    final_path = unique_path(destination_dir, sanitize_filename(stem), AUDIO_EXTENSION)
    final_stem = os.path.splitext(os.path.basename(final_path))[0]
    outtmpl = os.path.join(destination_dir, f"{final_stem}.%(ext)s")

    class _Cancelled(Exception):
        """Internal signal - yt-dlp only stops when a hook raises."""

    def _hook(status: dict[str, Any]) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise _Cancelled

        if status.get("status") != "downloading":
            return

        downloaded = int(status.get("downloaded_bytes") or 0)
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        total = int(total) if total else None

        if downloaded > max_bytes:
            raise YouTubeError(
                f"Download exceeded the {int(max_bytes / (1024 * 1024))} MB limit"
            )

        if progress_callback is not None:
            fraction = (downloaded / total) if total else 0.0
            progress_callback(min(fraction, 1.0), downloaded, total)

    options = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": AUDIO_FORMAT,
                "preferredquality": AUDIO_QUALITY,
            }
        ],
        "progress_hooks": [_hook],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # Home Assistant already logs; let yt-dlp raise instead of printing.
        "logger": _LOGGER,
        "retries": 3,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = _single_entry(ydl.extract_info(url, download=True), url)
    except _Cancelled as err:
        raise YouTubeError("Cancelled") from err
    except YouTubeError:
        raise
    except Exception as err:  # noqa: BLE001 - yt-dlp raises a wide variety
        raise YouTubeError(f"Could not download {url}: {err}") from err

    path = _find_audio_file(destination_dir, final_stem)
    if path is None:
        raise YouTubeError(
            "yt-dlp finished but no audio file was produced - is ffmpeg available?"
        )

    size = os.path.getsize(path)
    if size > max_bytes:
        os.remove(path)
        raise YouTubeError(
            f"Extracted audio is {int(size / (1024 * 1024))} MB which exceeds the "
            f"{int(max_bytes / (1024 * 1024))} MB limit"
        )

    duration = info.get("duration")
    return YouTubeResult(
        path=path,
        title=info.get("title") or final_stem,
        duration=float(duration) if duration else None,
        bytes_downloaded=size,
        final_url=info.get("webpage_url") or url,
    )


def _find_audio_file(directory: str, stem: str) -> str | None:
    """Return the converted MP3, falling back to whatever yt-dlp left behind."""
    preferred = os.path.join(directory, f"{stem}{AUDIO_EXTENSION}")
    if os.path.isfile(preferred):
        return preferred

    try:
        matches = [
            entry.path
            for entry in os.scandir(directory)
            if entry.is_file() and entry.name.startswith(f"{stem}.")
        ]
    except OSError:
        return None

    return matches[0] if matches else None
