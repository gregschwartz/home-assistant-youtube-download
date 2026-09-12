"""Discover the folders inside Home Assistant's media sources.

The card lets you pick where a download is filed, and the only places that make
sense are the media directories Home Assistant already serves (``/media`` by
default). Anything outside them is rejected so a crafted card or service call
cannot write elsewhere on disk.
"""
from __future__ import annotations

import logging
import os

from homeassistant.core import HomeAssistant

from .const import EXCLUDED_MEDIA_FOLDERS

_LOGGER = logging.getLogger(__name__)


class MediaFolderError(ValueError):
    """Raised when a requested folder is not usable."""


def media_sources(hass: HomeAssistant) -> dict[str, str]:
    """Return the configured media directories as ``{name: path}``."""
    return {
        name: os.path.realpath(path)
        for name, path in (hass.config.media_dirs or {}).items()
    }


async def async_list_media_folders(hass: HomeAssistant) -> list[dict[str, str]]:
    """Return every folder a download may be filed into, shallowest first."""
    return await hass.async_add_executor_job(_scan, media_sources(hass))


def preferred_media_folder(
    folders: list[dict[str, str]], preferred: str
) -> str | None:
    """Return the folder a YouTube download should pre-select.

    Matched in order of how specific the match is, so a setting can name a
    nested folder exactly:

    1. the full relative path (``meditations/morning``)
    2. the folder's own name (``morning``)
    3. any folder whose path contains the setting as a substring

    Going by relative path first matters once the same name appears twice -
    ``art/morning`` and ``meditations/morning`` are different places, and a
    bare name cannot tell them apart. No match means the card starts with
    nothing selected rather than guessing, because filing audio in the wrong
    place is worse than one extra click.
    """
    needle = (preferred or "").strip().strip("/").lower()
    if not needle:
        return None

    def _match(key) -> str | None:
        for folder in folders:
            if key(folder):
                return folder["path"]
        return None

    return (
        _match(lambda folder: folder["label"].lower().replace(os.sep, "/") == needle)
        or _match(lambda folder: os.path.basename(folder["path"]).lower() == needle)
        or _match(lambda folder: needle in folder["label"].lower().replace(os.sep, "/"))
    )


def resolve_media_folder(hass: HomeAssistant, folder: str) -> str:
    """Return the absolute path for ``folder``, or raise :class:`MediaFolderError`.

    Accepts either an absolute path or a path relative to a media source, and
    always checks the result really sits under one of them.
    """
    if not isinstance(folder, str) or not folder.strip():
        raise MediaFolderError("No media folder given")

    sources = media_sources(hass)
    if not sources:
        raise MediaFolderError(
            "Home Assistant has no media directories configured, so there is "
            "nowhere to save the download"
        )

    candidate = os.path.realpath(os.path.expanduser(folder.strip()))

    for root in sources.values():
        if candidate == root or candidate.startswith(root + os.sep):
            return candidate

    # Not absolute (or not under a source): try it as "<source>/<folder>".
    relative = folder.strip().lstrip("/")
    for root in sources.values():
        candidate = os.path.realpath(os.path.join(root, relative))
        if candidate == root or candidate.startswith(root + os.sep):
            if os.path.isdir(candidate):
                return candidate

    raise MediaFolderError(
        f"'{folder}' is not inside a Home Assistant media directory "
        f"({', '.join(sorted(sources.values()))})"
    )


def _scan(sources: dict[str, str]) -> list[dict[str, str]]:
    """Walk the media sources (blocking; executor only).

    The source root itself is not offered. Home Assistant names the default
    source "local", which means nothing to anyone looking at their own media
    folders, and dropping a download at the top level is rarely what you want.
    The exception is a source with no subfolders at all, where hiding the root
    would leave nowhere to save to.
    """
    folders: list[dict[str, str]] = []

    for name, root in sources.items():
        if not os.path.isdir(root):
            _LOGGER.debug("Media directory %s does not exist", root)
            continue

        below = _walk(root, root, name, seen={os.path.realpath(root)})
        if not below:
            folders.append({"path": root, "label": name, "source": name})
        folders.extend(below)

    return folders


def _walk(
    root: str, current: str, source: str, *, seen: set[str]
) -> list[dict[str, str]]:
    """Return every folder below ``current``, at any depth."""
    try:
        entries = sorted(os.scandir(current), key=lambda entry: entry.name.lower())
    except OSError as err:
        _LOGGER.debug("Could not list %s: %s", current, err)
        return []

    found: list[dict[str, str]] = []
    for entry in entries:
        # Hidden folders are HA/OS bookkeeping (@eaDir, .Trash, ...), not media.
        if entry.name.startswith((".", "@")) or not entry.is_dir(follow_symlinks=False):
            continue

        label = os.path.relpath(entry.path, root)
        if label.lower().replace(os.sep, "/") in EXCLUDED_MEDIA_FOLDERS:
            continue

        # The scan is unbounded in depth, so a symlink loop would hang it.
        real = os.path.realpath(entry.path)
        if real in seen:
            continue
        seen.add(real)

        found.append(
            {
                "path": entry.path,
                "label": label,
                "source": source,
            }
        )
        found.extend(_walk(root, entry.path, source, seen=seen))

    return found
