"""Orchestrates previews and downloads, and pushes progress to the card.

Jobs live in memory only. You asked for a progress bar and a result line, not a
history, so nothing is written to ``.storage`` and a restart starts clean.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field, fields
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_MAX_DOWNLOAD_MB,
    CONF_MAX_HISTORY,
    CONF_PREFERRED_FOLDER,
    DEFAULT_MAX_DOWNLOAD_MB,
    DEFAULT_MAX_HISTORY,
    DEFAULT_PREFERRED_FOLDER,
    EVENT_DOWNLOAD_COMPLETED,
    EVENT_DOWNLOAD_FAILED,
    FINISHED_STATES,
    KIND_IMAGE,
    KIND_YOUTUBE,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_DOWNLOADING,
    STATE_FAILED,
)
from .filenames import human_duration, human_size, sanitize_filename, stem_from_url
from .images import (
    ImageError,
    async_download_image,
    async_preview_image,
    looks_like_image_url,
)
from .media_folders import (
    MediaFolderError,
    async_list_media_folders,
    preferred_media_folder,
    resolve_media_folder,
)
from .updater import is_stale_error, upgrade
from .youtube import (
    YouTubeError,
    async_download_youtube,
    async_preview_youtube,
    is_youtube_url,
    normalize_youtube_ref,
)

_LOGGER = logging.getLogger(__name__)

# Progress pushes are cheap but not free; only send when the bar would move.
PROGRESS_STEP = 0.02


class DownloadError(Exception):
    """Raised when a download cannot be started or finished."""


@dataclass
class DownloadJob:
    """A single "paste a URL, save the file" unit of work."""

    id: str
    url: str
    kind: str
    title: str
    folder: str
    state: str = STATE_DOWNLOADING
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    path: str | None = None
    filename: str | None = None
    size: int | None = None
    size_display: str = ""
    created: str = ""
    finished: str | None = None

    cancel_event: threading.Event = field(
        default_factory=threading.Event, repr=False, compare=False
    )

    def to_dict(self) -> dict[str, Any]:
        """Return the job as the card sees it.

        Built field by field rather than with ``dataclasses.asdict``, which
        deep-copies every value: the cancel event holds a lock, and copying a
        lock raises ``TypeError``. Every remaining field is a primitive, so
        there is nothing to copy anyway.
        """
        return {
            field_.name: getattr(self, field_.name)
            for field_ in fields(self)
            if field_.name != "cancel_event"
        }

    @property
    def is_finished(self) -> bool:
        """Return True once the job will not change again."""
        return self.state in FINISHED_STATES


class DownloadManager:
    """Owns the job list and runs one download at a time per job."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Set up the manager for a config entry."""
        self.hass = hass
        self.entry = entry
        self._jobs: dict[str, DownloadJob] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._listeners: list[Callable[[DownloadJob | None], None]] = []
        # Filled in by the first upgrade (runs on the executor; reading the
        # version is file I/O that does not belong on the event loop).
        self.ytdlp_version: str | None = None
        # Serialises upgrades with each other and with download starts, so two
        # uv processes never race on one install and no download imports a
        # half-written yt-dlp. _idle is set while no YouTube download is
        # running, so an upgrade never swaps modules under one.
        self._ytdlp_lock = asyncio.Lock()
        self._inflight = 0
        self._idle = asyncio.Event()
        self._idle.set()

    # -- configuration -----------------------------------------------------

    @property
    def options(self) -> dict[str, Any]:
        """Return the effective options, defaults filled in."""
        data = {**self.entry.data, **self.entry.options}
        return {
            CONF_PREFERRED_FOLDER: data.get(
                CONF_PREFERRED_FOLDER, DEFAULT_PREFERRED_FOLDER
            ),
            CONF_MAX_DOWNLOAD_MB: data.get(
                CONF_MAX_DOWNLOAD_MB, DEFAULT_MAX_DOWNLOAD_MB
            ),
            CONF_MAX_HISTORY: data.get(CONF_MAX_HISTORY, DEFAULT_MAX_HISTORY),
        }

    @property
    def max_bytes(self) -> int:
        """Return the per-download size limit in bytes."""
        return int(self.options[CONF_MAX_DOWNLOAD_MB]) * 1024 * 1024

    # -- listeners ---------------------------------------------------------

    @callback
    def async_add_listener(
        self, listener: Callable[[DownloadJob | None], None]
    ) -> Callable[[], None]:
        """Subscribe to job changes; returns the unsubscribe callback."""
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    @callback
    def _notify(self, job: DownloadJob | None) -> None:
        """Tell every listener a job changed (or the whole list did)."""
        for listener in list(self._listeners):
            try:
                listener(job)
            except Exception:  # noqa: BLE001 - a bad listener must not stop others
                _LOGGER.exception("Download listener raised")

    def jobs_as_dicts(self) -> list[dict[str, Any]]:
        """Return every job, newest first."""
        return [
            job.to_dict()
            for job in sorted(
                self._jobs.values(), key=lambda job: job.created, reverse=True
            )
        ]

    # -- previews ----------------------------------------------------------

    def classify(self, url: str) -> str:
        """Return the kind of URL this is, or raise :class:`DownloadError`."""
        url = (url or "").strip()
        if is_youtube_url(url):
            return KIND_YOUTUBE
        if looks_like_image_url(url):
            return KIND_IMAGE
        raise DownloadError(
            "That does not look like a YouTube link or an http(s) image URL"
        )

    async def async_preview(self, url: str) -> dict[str, Any]:
        """Describe ``url`` so the card can show it before downloading.

        Also returns the folder to pre-select: the preferred one for a video,
        and deliberately nothing for an image so a destination must be chosen.
        """
        url = normalize_youtube_ref(url)
        kind = self.classify(url)
        folders = await async_list_media_folders(self.hass)

        if kind == KIND_YOUTUBE:
            try:
                preview = await async_preview_youtube(self.hass, url)
            except YouTubeError as err:
                raise DownloadError(str(err)) from err

            return {
                "kind": KIND_YOUTUBE,
                "url": preview.url,
                "title": preview.title,
                "subtitle": " · ".join(
                    part
                    for part in (human_duration(preview.duration), preview.uploader)
                    if part
                ),
                "thumbnail": preview.thumbnail,
                "image": None,
                "extension": ".mp3",
                "suggested_filename": sanitize_filename(preview.title),
                "suggested_folder": preferred_media_folder(
                    folders, self.options[CONF_PREFERRED_FOLDER]
                ),
            }

        try:
            preview = await async_preview_image(self.hass, url)
        except ImageError as err:
            raise DownloadError(str(err)) from err

        return {
            "kind": KIND_IMAGE,
            "url": preview.url,
            "title": preview.title,
            "subtitle": " · ".join(
                part
                for part in (preview.content_type, human_size(preview.size))
                if part
            ),
            "thumbnail": None,
            # The card renders the picture straight from this URL.
            "image": preview.url,
            "extension": os.path.splitext(preview.url)[1] or "",
            "suggested_filename": preview.title,
            # No default on purpose: pick where the picture goes.
            "suggested_folder": None,
        }

    # -- yt-dlp ------------------------------------------------------------

    async def async_upgrade_ytdlp(
        self, *, skip_if_busy: bool = False
    ) -> tuple[str | None, str | None]:
        """Upgrade yt-dlp off the event loop; returns (previous, current).

        Waits for in-flight YouTube downloads first (new ones queue on the
        lock). With ``skip_if_busy`` it gives up instead of waiting, which is
        what the unattended daily run wants.
        """
        async with self._ytdlp_lock:
            if skip_if_busy and self._inflight:
                _LOGGER.debug("Skipping yt-dlp upgrade: %d download(s) running", self._inflight)
                return self.ytdlp_version, self.ytdlp_version
            await self._idle.wait()
            old, new = await self.hass.async_add_executor_job(
                upgrade, self.hass.config.config_dir
            )
            # Compare against what entities currently show, not upgrade()'s
            # old/new: on first run the sensor holds None even if pip changed nothing.
            shown = self.ytdlp_version
            self.ytdlp_version = new
        if new != shown:
            self._notify(None)
        return old, new

    # -- downloads ---------------------------------------------------------

    async def async_start_download(
        self, url: str, *, folder: str, filename: str | None = None
    ) -> DownloadJob:
        """Validate the request, create a job, and run it in the background."""
        url = (url or "").strip()
        kind = self.classify(url)

        try:
            destination = resolve_media_folder(self.hass, folder)
        except MediaFolderError as err:
            raise DownloadError(str(err)) from err

        stem = sanitize_filename(
            filename or "",
            fallback=stem_from_url(url, fallback="download"),
        )

        job = DownloadJob(
            id=uuid.uuid4().hex,
            url=url,
            kind=kind,
            title=stem,
            folder=destination,
            message="Starting",
            created=dt_util.utcnow().isoformat(),
        )
        self._jobs[job.id] = job
        self._prune()
        self._notify(job)

        self._tasks[job.id] = self.hass.async_create_task(
            self._async_run(job, stem, destination),
            f"youtube_download {job.id}",
        )
        return job

    async def async_cancel_job(self, job_id: str) -> bool:
        """Ask a running download to stop. Returns False if it already ended."""
        job = self._jobs.get(job_id)
        if job is None or job.is_finished:
            return False

        job.cancel_event.set()
        job.message = "Cancelling"
        self._notify(job)
        return True

    def clear_finished(self) -> int:
        """Drop finished jobs from the list; returns how many went."""
        finished = [job_id for job_id, job in self._jobs.items() if job.is_finished]
        for job_id in finished:
            del self._jobs[job_id]
            self._tasks.pop(job_id, None)

        if finished:
            self._notify(None)
        return len(finished)

    async def async_shutdown(self) -> None:
        """Cancel everything still running, for config entry unload."""
        for job in self._jobs.values():
            if not job.is_finished:
                job.cancel_event.set()

        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._tasks.clear()
        self._listeners.clear()

    # -- internals ---------------------------------------------------------

    def _prune(self) -> None:
        """Keep the job list to the configured length, oldest finished first."""
        limit = int(self.options[CONF_MAX_HISTORY])
        if len(self._jobs) <= limit:
            return

        removable = sorted(
            (job for job in self._jobs.values() if job.is_finished),
            key=lambda job: job.created,
        )
        for job in removable:
            if len(self._jobs) <= limit:
                break
            del self._jobs[job.id]
            self._tasks.pop(job.id, None)

    @callback
    def _make_progress_callback(self, job: DownloadJob) -> Callable[..., None]:
        """Return a thread-safe progress hook for the downloaders."""
        last = 0.0

        def _report(fraction: float, downloaded: int, total: int | None) -> None:
            nonlocal last
            if fraction - last < PROGRESS_STEP and fraction < 1.0:
                return
            last = fraction

            def _apply() -> None:
                job.progress = fraction
                job.size = downloaded
                job.message = (
                    f"{human_size(downloaded)} of {human_size(total)}"
                    if total
                    else human_size(downloaded)
                )
                self._notify(job)

            self.hass.loop.call_soon_threadsafe(_apply)

        return _report

    async def _async_fetch(
        self, job: DownloadJob, stem: str, destination: str
    ) -> tuple[str, int]:
        """Download the job's URL once; returns (path, size)."""
        if job.kind != KIND_YOUTUBE:
            result = await self._async_download(async_download_image, job, stem, destination)
            return result.path, result.bytes_downloaded

        async with self._ytdlp_lock:  # wait out an in-flight upgrade
            self._inflight += 1
            self._idle.clear()
        try:
            result = await self._async_download(async_download_youtube, job, stem, destination)
        finally:
            self._inflight -= 1
            if not self._inflight:
                self._idle.set()
        return result.path, result.bytes_downloaded

    async def _async_download(
        self, download: Callable[..., Any], job: DownloadJob, stem: str, destination: str
    ) -> Any:
        return await download(
            self.hass,
            job.url,
            destination,
            stem=stem,
            max_bytes=self.max_bytes,
            progress_callback=self._make_progress_callback(job),
            cancel_event=job.cancel_event,
        )

    async def _async_run(self, job: DownloadJob, stem: str, destination: str) -> None:
        """Run the download and record how it went."""
        try:
            try:
                used = self.ytdlp_version
                path, size = await self._async_fetch(job, stem, destination)
            except YouTubeError as err:
                # A 403 / "sign in" usually means YouTube changed under a
                # stale yt-dlp; a fresh one fixes it more often than not.
                if job.cancel_event.is_set() or not is_stale_error(str(err)):
                    raise
                _, new = await self.async_upgrade_ytdlp()
                # Compare with the version this attempt ran on, not the one
                # before *this* upgrade call: another job may have upgraded
                # while we were downloading, and that still deserves a retry.
                if new == used:
                    raise
                job.message = f"Retrying with yt-dlp {new}"
                self._notify(job)
                path, size = await self._async_fetch(job, stem, destination)
        except asyncio.CancelledError:
            self._finish(job, STATE_CANCELLED, "Cancelled")
            raise
        except (YouTubeError, ImageError) as err:
            if job.cancel_event.is_set():
                self._finish(job, STATE_CANCELLED, "Cancelled")
            else:
                self._finish(job, STATE_FAILED, str(err))
            return
        except Exception as err:  # noqa: BLE001 - never let a job wedge the queue
            _LOGGER.exception("Download of %s failed", job.url)
            self._finish(job, STATE_FAILED, str(err))
            return

        job.path = path
        job.filename = os.path.basename(path)
        job.size = size
        job.size_display = human_size(size)
        job.progress = 1.0
        self._finish(job, STATE_COMPLETED, f"Saved as {job.filename}")

    @callback
    def _finish(self, job: DownloadJob, state: str, message: str) -> None:
        """Mark a job finished, notify the card, and fire the HA event."""
        job.state = state
        job.message = message
        job.error = message if state == STATE_FAILED else None
        job.finished = dt_util.utcnow().isoformat()
        if state != STATE_COMPLETED:
            job.progress = 0.0
        self._notify(job)

        if state == STATE_COMPLETED:
            self.hass.bus.async_fire(EVENT_DOWNLOAD_COMPLETED, job.to_dict())
        elif state == STATE_FAILED:
            self.hass.bus.async_fire(EVENT_DOWNLOAD_FAILED, job.to_dict())
