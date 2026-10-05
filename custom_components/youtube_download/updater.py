"""Keep yt-dlp current.

Home Assistant installs a requirement once and never upgrades it, but YouTube
changes often enough that a months-old yt-dlp stops working (HTTP 403, "sign
in to confirm"). So the integration upgrades it itself.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import logging
import sys

from homeassistant.requirements import pip_kwargs
from homeassistant.util.package import install_package

_LOGGER = logging.getLogger(__name__)

PACKAGE = "yt-dlp"

# Error fragments that almost always mean "YouTube changed, yt-dlp is stale".
STALE_MARKERS = ("403", "Sign in to confirm", "unable to download video data")


def installed_version() -> str | None:
    """Return the installed yt-dlp version, or None if it is missing."""
    try:
        return importlib.metadata.version(PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        return None


def is_stale_error(message: str) -> bool:
    """Return True when a download error is the kind an upgrade tends to fix."""
    return any(marker in message for marker in STALE_MARKERS)


def upgrade(config_dir: str | None = None) -> tuple[str | None, str | None]:
    """Upgrade yt-dlp in place (blocking; executor only). Returns (old, new).

    Never raises: a failed upgrade is logged and leaves the old version in use.
    """
    old = installed_version()
    try:
        # Same pip flags HA used for the first install, so the upgrade lands in
        # the same place (notably the deps/ dir on non-venv installs).
        kwargs = {"upgrade": True, **pip_kwargs(config_dir)}
        if not install_package(PACKAGE, **kwargs):
            raise RuntimeError("pip reported a failure")
    except Exception as err:  # noqa: BLE001 - an upgrade must never break setup
        _LOGGER.warning("Could not upgrade yt-dlp (still %s): %s", old, err)
        return old, old

    # Forget the already-imported copy so the next lazy import loads the new one.
    importlib.invalidate_caches()
    for name in [n for n in sys.modules if n == "yt_dlp" or n.startswith("yt_dlp.")]:
        del sys.modules[name]

    new = installed_version()
    if new != old:
        _LOGGER.info("yt-dlp upgraded from %s to %s", old, new)
    return old, new
