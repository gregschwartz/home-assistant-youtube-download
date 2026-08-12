"""Turning titles and URLs into filenames that are safe to write."""
from __future__ import annotations

import os
import re
from urllib.parse import unquote, urlparse

# Anything a filesystem (or a media browser) is likely to choke on.
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")

MAX_STEM_LENGTH = 120


def sanitize_filename(name: str, *, fallback: str = "download") -> str:
    """Return ``name`` reduced to a safe filename stem."""
    # Collapse whitespace first: tabs and newlines are control characters, and
    # deleting them outright would run the words either side together.
    cleaned = _WHITESPACE.sub(" ", name or "")
    cleaned = _UNSAFE.sub("", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip().strip(".")
    cleaned = cleaned[:MAX_STEM_LENGTH].strip()
    return cleaned or fallback


def stem_from_url(url: str, *, fallback: str = "download") -> str:
    """Return a filename stem taken from the last path segment of ``url``."""
    try:
        path = urlparse(url).path
    except ValueError:
        return fallback

    base = os.path.basename(unquote(path))
    stem = os.path.splitext(base)[0]
    return sanitize_filename(stem, fallback=fallback)


def extension_from_url(url: str) -> str:
    """Return the lowercased extension in ``url``'s path, or an empty string."""
    try:
        path = urlparse(url).path
    except ValueError:
        return ""

    ext = os.path.splitext(unquote(path))[1].lower()
    # Guard against a query-string-ish tail being read as an extension.
    return ext if 1 < len(ext) <= 6 and ext[1:].isalnum() else ""


def unique_path(directory: str, stem: str, extension: str) -> str:
    """Return a path in ``directory`` that no file occupies yet.

    Collisions get " (2)", " (3)" and so on appended, so re-downloading the same
    video never silently replaces the copy already filed.
    """
    candidate = os.path.join(directory, f"{stem}{extension}")
    if not os.path.exists(candidate):
        return candidate

    for counter in range(2, 1000):
        candidate = os.path.join(directory, f"{stem} ({counter}){extension}")
        if not os.path.exists(candidate):
            return candidate

    raise OSError(f"Could not find a free filename for '{stem}{extension}'")


def human_size(num_bytes: int | None) -> str:
    """Return ``num_bytes`` as a short human-readable string."""
    if not num_bytes:
        return ""

    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def human_duration(seconds: float | None) -> str:
    """Return ``seconds`` as ``H:MM:SS`` or ``M:SS``."""
    if not seconds or seconds < 0:
        return ""

    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
