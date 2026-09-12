# YouTube Download for Home Assistant

Paste a YouTube link or an image URL into a Lovelace card, check the preview is
the right thing, pick a folder, hit Download.

I built this for two things: pulling down YouTube meditations as MP3s onto my
meditation-app media source, and saving art from around the web onto the
folder my art TV displays as a slideshow. The two paths in the card map
directly to that — YouTube links become audio, image URLs get saved as-is —
but there's nothing meditation- or art-specific baked in beyond the default
folder name, so it works just as well for podcasts, sound effects, wallpapers,
or whatever else you're filing into Home Assistant's media directories.

- **YouTube links** are saved as MP3 (yt-dlp grabs the best audio stream, ffmpeg
  converts it), and pre-select your meditations folder.
- **Image URLs** are saved as-is, and pre-select nothing — you choose where each
  picture goes.

Everything lands inside Home Assistant's configured media directories. Nothing
else on disk is writable from the card.

## Installation

### HACS (recommended)

1. HACS → Integrations → ⋮ → **Custom repositories**
2. Add `https://github.com/gregschwartz/home-assistant-youtube-download` as an
   **Integration**
3. Install **YouTube Download**, then restart Home Assistant

### Manual

Copy `custom_components/youtube_download/` into your Home Assistant
`config/custom_components/` folder and restart.

## Setup

1. **Settings → Devices & Services → Add Integration → YouTube Download**
2. Set:
   - **Folder to pre-select for YouTube links** — defaults to
     `meditations/morning`. Matched case-insensitively, most specific first:
     the full relative path (`meditations/morning`), then the folder's own name
     (`morning`), then any folder containing the text. Use a relative path when
     the same name appears twice — `art/morning` and `meditations/morning` are
     different places. Leave blank to always choose by hand.
   - **Maximum download size (MB)** — default 500.
   - **Downloads to keep in the card** — default 20. The list is in-memory only
     and starts empty after a restart.
3. Add the card to a dashboard: **Edit dashboard → Add card → YouTube Download**.

The card's JavaScript resource is registered automatically on storage-mode
dashboards. If you use a YAML dashboard, add it yourself:

```yaml
lovelace:
  resources:
    - url: /youtube_download/youtube-download-card.js
      type: module
```

### Card options

```yaml
type: custom:youtube-download-card
title: Download to media       # optional
max_jobs: 5                    # optional, downloads shown under the button
```

## How folders are found

Every folder under every directory in your `media_dirs` configuration is
offered, at any depth. Hidden folders and NAS bookkeeping directories (`.`, `@`
prefixes) are skipped, and symlink loops are detected rather than followed.

The source root itself is not offered — Home Assistant names the default source
`local`, which means nothing to anyone looking at their own media folders. The
exception is a source with no subfolders, where hiding the root would leave
nowhere to save to.

By default that's `/media`. To add more:

```yaml
homeassistant:
  media_dirs:
    media: /media
    photos: /mnt/photos
```

## Filenames

The filename field is pre-filled with the video title (or the image URL's last
path segment) and you can edit it before downloading. The extension is added for
you. A name already in use gets ` (2)`, ` (3)` and so on appended, so
re-downloading something never silently replaces the copy you already have.

## Services

Everything the card does is also available as a service.

### `youtube_download.download_url`

| Field | Required | Description |
|---|---|---|
| `url` | yes | A YouTube link, or a direct http(s) URL to an image |
| `folder` | yes | Absolute path inside a media directory, or a path relative to one |
| `filename` | no | Filename without extension |

Returns `{"job_id": "..."}`.

```yaml
action: youtube_download.download_url
data:
  url: https://www.youtube.com/watch?v=dQw4w9WgXcQ
  folder: /media/meditations
  filename: Evening Wind Down
```

### `youtube_download.cancel_job`

Stops a running download. Takes `job_id`, returns `{"cancelled": true|false}`.

### `youtube_download.list_media_folders`

Returns every folder a download can go into, plus the one a YouTube URL
pre-selects.

## Events

| Event | Fired when |
|---|---|
| `youtube_download_completed` | A file finished downloading |
| `youtube_download_failed` | A download failed |

Both carry the job: `id`, `url`, `kind`, `folder`, `path`, `filename`, `size`,
`state`, `message`.

```yaml
triggers:
  - trigger: event
    event_type: youtube_download_completed
actions:
  - action: notify.mobile_app
    data:
      message: "Saved {{ trigger.event.data.filename }}"
```

## Requirements

- Home Assistant 2024.11 or newer
- `yt-dlp` (installed automatically)
- ffmpeg, which Home Assistant ships with — needed to convert YouTube audio to
  MP3

Downloading a video requires that you have the right to do so. YouTube's terms
permit downloading only where YouTube provides a download button or with the
copyright holder's permission.

## Development

```bash
python -m pytest -q youtube_download_test.py
```

The tests cover filename handling and the media-folder scanner, and run without
Home Assistant installed.

## License

MIT — see [LICENSE](LICENSE).
