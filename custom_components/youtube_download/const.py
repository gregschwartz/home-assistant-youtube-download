"""Constants for the YouTube Download integration."""
from typing import Final

DOMAIN: Final = "youtube_download"

# URL the bundled Lovelace card is served from
FRONTEND_URL: Final = f"/{DOMAIN}/youtube-download-card.js"
FRONTEND_FILENAME: Final = "youtube-download-card.js"

# Configuration keys
CONF_PREFERRED_FOLDER: Final = "preferred_folder"
CONF_MAX_DOWNLOAD_MB: Final = "max_download_mb"
CONF_MAX_HISTORY: Final = "max_history"

# Defaults
#
# A YouTube URL pre-selects the first folder whose name contains this, which is
# the whole point of the setting: meditations arrive far more often than
# anything else. Images deliberately pre-select nothing.
DEFAULT_PREFERRED_FOLDER: Final = "meditation"
DEFAULT_MAX_DOWNLOAD_MB: Final = 500
DEFAULT_MAX_HISTORY: Final = 20

# Job states
STATE_DOWNLOADING: Final = "downloading"
STATE_COMPLETED: Final = "completed"
STATE_FAILED: Final = "failed"
STATE_CANCELLED: Final = "cancelled"

FINISHED_STATES: Final = (STATE_COMPLETED, STATE_FAILED, STATE_CANCELLED)

# Kinds of URL the card can handle
KIND_YOUTUBE: Final = "youtube"
KIND_IMAGE: Final = "image"

# Services
SERVICE_DOWNLOAD_URL: Final = "download_url"
SERVICE_CANCEL_JOB: Final = "cancel_job"
SERVICE_LIST_MEDIA_FOLDERS: Final = "list_media_folders"

# Events
EVENT_DOWNLOAD_COMPLETED: Final = f"{DOMAIN}_download_completed"
EVENT_DOWNLOAD_FAILED: Final = f"{DOMAIN}_download_failed"

# Hosts handled by yt-dlp rather than a plain HTTP download.
YOUTUBE_HOSTS: Final = (
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
)

# Image content types the downloader accepts, mapped to the extension used when
# the URL itself does not carry a usable one.
IMAGE_EXTENSIONS: Final = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/svg+xml": ".svg",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/avif": ".avif",
}

# Extensions recognised straight from the URL, so an image URL that answers a
# HEAD request badly still gets identified.
IMAGE_SUFFIXES: Final = tuple(sorted(set(IMAGE_EXTENSIONS.values()) | {".jpeg"}))
