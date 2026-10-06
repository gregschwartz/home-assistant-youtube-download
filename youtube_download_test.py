"""Tests for the pure logic in the YouTube Download integration.

These deliberately avoid importing Home Assistant: the modules under test are
the ones that only touch the filesystem and strings, so they run in plain
pytest with nothing installed.
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import os
import sys
import types
import uuid
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parent / "custom_components" / "youtube_download"


def _load(name: str):
    """Import a module from the component without its package dependencies."""
    package = "youtube_download_under_test"
    if package not in sys.modules:
        stub = types.ModuleType(package)
        stub.__path__ = [str(COMPONENT)]
        sys.modules[package] = stub

    full = f"{package}.{name}"
    if full in sys.modules:
        return sys.modules[full]

    spec = importlib.util.spec_from_file_location(full, COMPONENT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


def _module(name: str, **attrs) -> types.ModuleType:
    """Register a stub module under ``name``."""
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _stub_dependencies() -> None:
    """Register just enough of Home Assistant and aiohttp to import the code.

    CI installs pytest and nothing else, so the handful of names these modules
    touch at import time get stood up here. Anything already installed (a dev
    machine running Home Assistant) is left alone.
    """
    if "homeassistant" not in sys.modules:
        try:
            import homeassistant  # noqa: F401
        except ImportError:
            import datetime

            _module("homeassistant")
            _module("homeassistant.config_entries", ConfigEntry=object)
            _module(
                "homeassistant.core",
                HomeAssistant=object,
                callback=lambda func: func,
            )
            _module("homeassistant.helpers")
            _module("homeassistant.requirements", pip_kwargs=lambda config_dir: {})
            _module("homeassistant.util.package", install_package=lambda *a, **k: True)
            _module(
                "homeassistant.helpers.aiohttp_client",
                async_get_clientsession=None,
            )
            _module(
                "homeassistant.util",
                dt=_module(
                    "homeassistant.util.dt",
                    utcnow=lambda: datetime.datetime.now(datetime.timezone.utc),
                ),
            )

    try:
        import aiohttp  # noqa: F401
    except ImportError:
        _module(
            "aiohttp",
            ClientTimeout=lambda **kwargs: None,
            ClientError=type("ClientError", (Exception,), {}),
            ClientResponse=object,
        )


# Stubs must be in place before the first module that imports them is loaded.
_stub_dependencies()

const = _load("const")
filenames = _load("filenames")
media_folders = _load("media_folders")
manager = _load("manager")
youtube = _load("youtube")
updater = _load("updater")


# -- filenames ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10 Minute Morning Meditation", "10 Minute Morning Meditation"),
        ("A/B: test?", "AB test"),
        ("  padded  ", "padded"),
        ("...", "download"),
        ("", "download"),
        ("a\tb\nc", "a b c"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert filenames.sanitize_filename(raw) == expected


def test_sanitize_filename_truncates():
    assert len(filenames.sanitize_filename("x" * 500)) == filenames.MAX_STEM_LENGTH


def test_sanitize_filename_uses_fallback():
    assert filenames.sanitize_filename("///", fallback="image") == "image"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/photos/sunset.jpg", "sunset"),
        ("https://example.com/photos/holiday%20snap.png", "holiday snap"),
        ("https://example.com/", "download"),
        ("https://example.com/a/b/c", "c"),
    ],
)
def test_stem_from_url(url, expected):
    assert filenames.stem_from_url(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/a.JPG", ".jpg"),
        ("https://example.com/a.png?width=200", ".png"),
        ("https://example.com/a", ""),
        ("https://example.com/a.verylongextension", ""),
    ],
)
def test_extension_from_url(url, expected):
    assert filenames.extension_from_url(url) == expected


def test_unique_path_avoids_overwriting(tmp_path):
    first = filenames.unique_path(str(tmp_path), "Meditation", ".mp3")
    assert first.endswith("Meditation.mp3")

    Path(first).touch()
    second = filenames.unique_path(str(tmp_path), "Meditation", ".mp3")
    assert second.endswith("Meditation (2).mp3")

    Path(second).touch()
    third = filenames.unique_path(str(tmp_path), "Meditation", ".mp3")
    assert third.endswith("Meditation (3).mp3")


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(None, ""), (0, ""), (62, "1:02"), (3661, "1:01:01")],
)
def test_human_duration(seconds, expected):
    assert filenames.human_duration(seconds) == expected


def test_human_size():
    assert filenames.human_size(None) == ""
    assert filenames.human_size(512) == "512 B"
    assert filenames.human_size(2 * 1024 * 1024) == "2.0 MB"


# -- media folders --------------------------------------------------------


def _tree(root: Path) -> None:
    """Build a small media tree with the shapes the scanner must handle.

    Mirrors the real layout: "morning" appears under both art and meditations,
    so a bare folder name cannot identify one of them.
    """
    (root / "meditations").mkdir()
    (root / "meditations" / "morning").mkdir()
    (root / "meditations" / "morning" / "short").mkdir()
    (root / "meditations" / "sleep").mkdir()
    (root / "art").mkdir()
    (root / "art" / "morning").mkdir()
    (root / "photos").mkdir()
    (root / ".hidden").mkdir()
    (root / "@eaDir").mkdir()
    (root / "photos" / "note.txt").write_text("not a folder")


def test_scan_is_fully_recursive_and_skips_bookkeeping(tmp_path):
    _tree(tmp_path)
    folders = media_folders._scan({"local": str(tmp_path)})
    labels = [folder["label"] for folder in folders]

    # The source root is hidden: "local" means nothing to the person choosing.
    assert "local" not in labels
    assert str(tmp_path) not in [folder["path"] for folder in folders]
    assert "meditations" in labels
    assert os.path.join("meditations", "morning") in labels
    assert os.path.join("meditations", "morning", "short") in labels
    assert "photos" in labels
    assert ".hidden" not in labels
    assert "@eaDir" not in labels


def test_scan_hides_housekeeping_destinations(tmp_path):
    (tmp_path / "art").mkdir()
    (tmp_path / "art" / "_Edit").mkdir()
    (tmp_path / "art" / "_Errors").mkdir()
    (tmp_path / "art" / "_Junk").mkdir()
    (tmp_path / "sounds").mkdir()
    (tmp_path / "sounds" / "temp").mkdir()
    (tmp_path / "sounds" / "temp" / "chime_tts").mkdir()

    folders = media_folders._scan({"local": str(tmp_path)})
    labels = [folder["label"] for folder in folders]

    assert os.path.join("art", "_Edit") not in labels
    assert os.path.join("art", "_Errors") not in labels
    assert os.path.join("art", "_Junk") not in labels
    assert os.path.join("sounds", "temp") not in labels
    assert os.path.join("sounds", "temp", "chime_tts") not in labels
    assert "art" in labels
    assert "sounds" in labels


def test_scan_survives_a_symlink_loop(tmp_path):
    _tree(tmp_path)
    (tmp_path / "meditations" / "loop").symlink_to(tmp_path, target_is_directory=True)

    folders = media_folders._scan({"local": str(tmp_path)})

    assert len(folders) < 30  # would not terminate without loop protection
    assert any(folder["label"] == "meditations" for folder in folders)


def test_scan_ignores_a_missing_source(tmp_path):
    assert media_folders._scan({"local": str(tmp_path / "nope")}) == []


def test_scan_keeps_the_root_when_there_is_nothing_below_it(tmp_path):
    """Hiding the root of an empty source would leave nowhere to save to."""
    folders = media_folders._scan({"local": str(tmp_path)})

    assert [folder["label"] for folder in folders] == ["local"]


def test_preferred_media_folder_matches_a_nested_path_exactly(tmp_path):
    """"morning" exists under both art and meditations; the path decides."""
    _tree(tmp_path)
    folders = media_folders._scan({"local": str(tmp_path)})

    picked = media_folders.preferred_media_folder(folders, "meditations/morning")
    assert picked == str(tmp_path / "meditations" / "morning")


def test_preferred_media_folder_is_case_and_slash_insensitive(tmp_path):
    _tree(tmp_path)
    folders = media_folders._scan({"local": str(tmp_path)})

    for setting in ("MEDITATIONS/MORNING", "/meditations/morning/"):
        assert media_folders.preferred_media_folder(folders, setting) == str(
            tmp_path / "meditations" / "morning"
        )


def test_preferred_media_folder_falls_back_to_a_bare_name(tmp_path):
    """An exact folder name still works, and beats a substring match."""
    _tree(tmp_path)
    folders = media_folders._scan({"local": str(tmp_path)})

    assert media_folders.preferred_media_folder(folders, "sleep") == str(
        tmp_path / "meditations" / "sleep"
    )


def test_preferred_media_folder_falls_back_to_a_substring(tmp_path):
    """The old loose setting keeps working after the folder split."""
    _tree(tmp_path)
    folders = media_folders._scan({"local": str(tmp_path)})

    assert media_folders.preferred_media_folder(folders, "meditation") == str(
        tmp_path / "meditations"
    )


def test_preferred_media_folder_returns_none_without_a_match(tmp_path):
    _tree(tmp_path)
    folders = media_folders._scan({"local": str(tmp_path)})

    assert media_folders.preferred_media_folder(folders, "podcasts") is None
    # Blank means "always choose manually", which images rely on.
    assert media_folders.preferred_media_folder(folders, "") is None


# -- the job model --------------------------------------------------------


def _job(**overrides):
    """Build a job with the fields the manager always sets."""
    defaults = {
        "id": "abc123",
        "url": "https://youtu.be/xyz",
        "kind": const.KIND_YOUTUBE,
        "title": "Morning Meditation",
        "folder": "/media/meditations",
    }
    return manager.DownloadJob(**{**defaults, **overrides})


def test_to_dict_survives_the_cancel_event():
    """dataclasses.asdict() deep-copies, and a lock cannot be deep-copied.

    Regression: to_dict() used asdict() and raised TypeError, which _notify()
    swallowed - so downloads ran but never appeared in the card.
    """
    data = _job().to_dict()

    assert data["id"] == "abc123"
    assert data["title"] == "Morning Meditation"
    assert data["state"] == const.STATE_DOWNLOADING
    assert "cancel_event" not in data


def test_to_dict_is_json_serialisable():
    """The card receives this over the websocket, so it has to survive JSON."""
    import json

    job = _job(progress=0.5, size=1024, filename="Morning Meditation.mp3")
    assert json.loads(json.dumps(job.to_dict()))["progress"] == 0.5


def test_to_dict_covers_every_field_but_the_event():
    """A new field must reach the card without anyone remembering to add it."""
    from dataclasses import fields

    expected = {f.name for f in fields(manager.DownloadJob)} - {"cancel_event"}
    assert set(_job().to_dict()) == expected


def test_is_finished_tracks_state():
    assert not _job().is_finished
    assert _job(state=const.STATE_COMPLETED).is_finished
    assert _job(state=const.STATE_FAILED).is_finished
    assert _job(state=const.STATE_CANCELLED).is_finished


# -- constants ------------------------------------------------------------


def test_image_suffixes_cover_the_content_types():
    for extension in const.IMAGE_EXTENSIONS.values():
        assert extension in const.IMAGE_SUFFIXES


def test_youtube_hosts_are_lowercase():
    assert all(host == host.lower() for host in const.YOUTUBE_HOSTS)


# -- youtube refs ---------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("u7deClndzQw", "https://www.youtube.com/watch?v=u7deClndzQw"),
        ("PL7Yvr29YiYrDv-9kxFsIoEavmPb8b8nif", "https://www.youtube.com/playlist?list=PL7Yvr29YiYrDv-9kxFsIoEavmPb8b8nif"),
        ("  https://youtu.be/u7deClndzQw  ", "https://www.youtube.com/watch?v=u7deClndzQw"),
        ("https://example.com/a.jpg", "https://example.com/a.jpg"),
    ],
)
def test_normalize_youtube_ref(ref, expected):
    assert youtube.normalize_youtube_ref(ref) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=u7deClndzQw&list=PLabc", "PLabc"),
        ("https://www.youtube.com/playlist?list=PLabc", "PLabc"),
        ("https://www.youtube.com/watch?v=u7deClndzQw", None),
        ("https://example.com/?list=PLabc", None),
    ],
)
def test_playlist_id(url, expected):
    assert youtube.playlist_id(url) == expected


# -- the yt-dlp updater ---------------------------------------------------


def _fake_install(monkeypatch, versions: list[str | None], result: bool = True):
    """Make install_package "upgrade" by advancing through ``versions``."""
    calls: list[tuple] = []

    def version(name: str) -> str:
        if versions[0] is None:
            raise importlib.metadata.PackageNotFoundError(name)
        return versions[0]

    def install(package: str, **kwargs) -> bool:
        calls.append((package, kwargs))
        if result and len(versions) > 1:
            versions.pop(0)
        return result

    monkeypatch.setattr(updater.importlib.metadata, "version", version)
    monkeypatch.setattr(updater, "install_package", install)
    monkeypatch.setattr(updater, "pip_kwargs", lambda config_dir: {})
    return calls


def test_installed_version_is_none_when_missing(monkeypatch):
    _fake_install(monkeypatch, [None])
    assert updater.installed_version() is None


def test_upgrade_reports_versions_and_drops_loaded_modules(monkeypatch):
    calls = _fake_install(monkeypatch, ["2026.1.1", "2026.9.9"])
    for name in ("yt_dlp", "yt_dlp.utils", "yt_dlp.extractor.youtube"):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["yt_dlp_plugins"] = types.ModuleType("yt_dlp_plugins")

    assert updater.upgrade("/config") == ("2026.1.1", "2026.9.9")

    assert calls == [("yt-dlp", {"upgrade": True})]
    assert not any(n == "yt_dlp" or n.startswith("yt_dlp.") for n in sys.modules)
    # Only yt-dlp itself is reloaded; look-alike names are not our business.
    assert "yt_dlp_plugins" in sys.modules
    del sys.modules["yt_dlp_plugins"]


def test_upgrade_installs_where_home_assistant_does(monkeypatch):
    calls = _fake_install(monkeypatch, ["1"])
    monkeypatch.setattr(updater, "pip_kwargs", lambda d: {"target": f"{d}/deps"})
    updater.upgrade("/config")
    assert calls[0][1] == {"upgrade": True, "target": "/config/deps"}


def test_upgrade_never_raises(monkeypatch):
    _fake_install(monkeypatch, ["2026.1.1", "2026.9.9"], result=False)
    assert updater.upgrade() == ("2026.1.1", "2026.1.1")

    def boom(package: str, **kwargs) -> bool:
        raise OSError("no network")

    monkeypatch.setattr(updater, "install_package", boom)
    assert updater.upgrade() == ("2026.1.1", "2026.1.1")


@pytest.mark.parametrize(
    ("message", "stale"),
    [
        ("HTTP Error 403: Forbidden", True),
        ("Sign in to confirm you're not a bot", True),
        ("unable to download video data: timed out", True),
        ("Download exceeded the 500 MB limit", False),
        ("Cancelled", False),
    ],
)
def test_is_stale_error(message, stale):
    assert updater.is_stale_error(message) is stale


# -- retry after a yt-dlp upgrade -----------------------------------------


class _FakeHass:
    """Just enough of HomeAssistant for DownloadManager._async_run."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.bus = types.SimpleNamespace(
            async_fire=lambda event, data: self.events.append(event)
        )
        self.config = types.SimpleNamespace(config_dir="/config")

    async def async_add_executor_job(self, func, *args):
        return func(*args)


def _run_download(monkeypatch, errors: list[str], versions: tuple):
    """Run one YouTube job whose downloader fails with ``errors`` first."""
    hass = _FakeHass()
    entry = types.SimpleNamespace(entry_id="e1", data={}, options={})
    mgr = manager.DownloadManager(hass, entry)
    mgr.ytdlp_version = versions[0]  # the version the first attempt runs on
    attempts: list[str] = []
    upgrades: list[str] = []

    async def fake_download(hass, url, destination, **kwargs):
        attempts.append(url)
        if errors:
            raise youtube.YouTubeError(errors.pop(0))
        return youtube.YouTubeResult(
            path=f"{destination}/x.mp3", title="x", duration=1.0,
            bytes_downloaded=10, final_url=url,
        )

    def fake_upgrade(config_dir: str | None = None) -> tuple:
        upgrades.append(config_dir)
        return versions

    monkeypatch.setattr(manager, "async_download_youtube", fake_download)
    monkeypatch.setattr(manager, "upgrade", fake_upgrade)

    job = manager.DownloadJob(
        id="j", url="https://youtu.be/abc", kind=const.KIND_YOUTUBE,
        title="t", folder="/media",
    )
    mgr._jobs[job.id] = job
    asyncio.run(mgr._async_run(job, "t", "/media"))
    return job, attempts, upgrades, mgr


def test_stale_error_upgrades_and_retries_once(monkeypatch):
    job, attempts, upgrades, mgr = _run_download(
        monkeypatch, ["HTTP Error 403: Forbidden"], ("1", "2")
    )
    assert (len(attempts), len(upgrades)) == (2, 1)
    assert job.state == const.STATE_COMPLETED
    assert mgr.ytdlp_version == "2"


def _no_sleep(monkeypatch):
    async def fake_sleep(delay):
        pass

    monkeypatch.setattr(manager.asyncio, "sleep", fake_sleep)


def test_unchanged_upgrade_falls_back_to_the_delayed_retry(monkeypatch):
    _no_sleep(monkeypatch)
    job, attempts, upgrades, _ = _run_download(
        monkeypatch, ["Sign in to confirm you're not a bot"] * 2, ("1", "1")
    )
    assert (len(attempts), len(upgrades)) == (2, 1)
    assert job.state == const.STATE_FAILED
    assert job.error == "Sign in to confirm you're not a bot"


def test_other_errors_do_not_trigger_an_upgrade(monkeypatch):
    _no_sleep(monkeypatch)
    job, attempts, upgrades, _ = _run_download(
        monkeypatch, ["Download exceeded the 500 MB limit"] * 2, ("1", "2")
    )
    assert (len(attempts), len(upgrades)) == (2, 0)
    assert job.state == const.STATE_FAILED


def test_second_failure_surfaces_the_new_error(monkeypatch):
    job, attempts, _, _ = _run_download(
        monkeypatch, ["HTTP Error 403: Forbidden", "still broken"], ("1", "2")
    )
    assert len(attempts) == 2
    assert job.error == "still broken"


def _make_job(mgr) -> "manager.DownloadJob":
    job = manager.DownloadJob(
        id=uuid.uuid4().hex, url="https://youtu.be/abc", kind=const.KIND_YOUTUBE,
        title="t", folder="/media",
    )
    mgr._jobs[job.id] = job
    return job


def test_concurrent_stale_failures_both_retry_after_one_upgrade(monkeypatch):
    """Two 403s at once: the second job must not see "no change" and give up."""
    hass = _FakeHass()
    mgr = manager.DownloadManager(hass, types.SimpleNamespace(entry_id="e1", data={}, options={}))
    jobs = [_make_job(mgr), _make_job(mgr)]
    failed: set[str] = set()
    installed = ["1"]

    async def fake_download(hass, url, destination, **kwargs):
        await asyncio.sleep(0)  # let both jobs fail before either upgrades
        if (job_id := kwargs["stem"]) not in failed:
            failed.add(job_id)
            raise youtube.YouTubeError("HTTP Error 403: Forbidden")
        return youtube.YouTubeResult(
            path="/media/x.mp3", title="x", duration=1.0, bytes_downloaded=1, final_url=url,
        )

    def fake_upgrade(config_dir=None):
        old = installed[0]
        installed[0] = "2"
        return old, "2"

    monkeypatch.setattr(manager, "async_download_youtube", fake_download)
    monkeypatch.setattr(manager, "upgrade", fake_upgrade)

    async def run():
        await asyncio.gather(*(mgr._async_run(j, j.id, "/media") for j in jobs))

    asyncio.run(run())
    assert [j.state for j in jobs] == [const.STATE_COMPLETED] * 2


def test_download_waits_for_an_upgrade_in_progress(monkeypatch):
    """A download started during an upgrade must not import yt-dlp mid-install."""
    hass = _FakeHass()
    release = asyncio.Event()
    order: list[str] = []

    async def slow_executor(func, *args):
        if func is manager.upgrade:
            order.append("upgrade start")
            await release.wait()
            order.append("upgrade done")
        return func(*args)

    hass.async_add_executor_job = slow_executor
    mgr = manager.DownloadManager(hass, types.SimpleNamespace(entry_id="e1", data={}, options={}))
    job = _make_job(mgr)

    async def fake_download(hass, url, destination, **kwargs):
        order.append("download")
        return youtube.YouTubeResult(
            path="/media/x.mp3", title="x", duration=1.0, bytes_downloaded=1, final_url=url,
        )

    monkeypatch.setattr(manager, "async_download_youtube", fake_download)
    monkeypatch.setattr(manager, "upgrade", lambda d=None: ("1", "2"))

    async def run():
        upgrade = asyncio.ensure_future(mgr.async_upgrade_ytdlp())
        await asyncio.sleep(0)
        download = asyncio.ensure_future(mgr._async_run(job, "t", "/media"))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(upgrade, download)

    asyncio.run(run())
    assert order == ["upgrade start", "upgrade done", "download"]


def _blocking_download(order: list[str], release: asyncio.Event):
    async def fake_download(hass, url, destination, **kwargs):
        order.append("download start")
        await release.wait()
        order.append("download done")
        return youtube.YouTubeResult(
            path="/media/x.mp3", title="x", duration=1.0, bytes_downloaded=1, final_url=url,
        )
    return fake_download


def test_service_upgrade_waits_for_running_download(monkeypatch):
    hass = _FakeHass()
    mgr = manager.DownloadManager(hass, types.SimpleNamespace(entry_id="e1", data={}, options={}))
    job = _make_job(mgr)
    order: list[str] = []
    release = asyncio.Event()
    monkeypatch.setattr(manager, "async_download_youtube", _blocking_download(order, release))
    monkeypatch.setattr(manager, "upgrade", lambda d=None: order.append("upgrade") or ("1", "2"))

    async def run():
        download = asyncio.ensure_future(mgr._async_run(job, "t", "/media"))
        await asyncio.sleep(0)
        upgrade = asyncio.ensure_future(mgr.async_upgrade_ytdlp())
        await asyncio.sleep(0)
        assert order == ["download start"]
        release.set()
        return await asyncio.gather(download, upgrade)

    _, result = asyncio.run(run())
    assert order == ["download start", "download done", "upgrade"]
    assert result == ("1", "2") and mgr.ytdlp_version == "2"


def test_daily_upgrade_skips_while_a_download_runs(monkeypatch):
    hass = _FakeHass()
    mgr = manager.DownloadManager(hass, types.SimpleNamespace(entry_id="e1", data={}, options={}))
    mgr.ytdlp_version = "1"
    job = _make_job(mgr)
    order: list[str] = []
    release = asyncio.Event()
    monkeypatch.setattr(manager, "async_download_youtube", _blocking_download(order, release))
    monkeypatch.setattr(manager, "upgrade", lambda d=None: order.append("upgrade") or ("1", "2"))

    async def run():
        download = asyncio.ensure_future(mgr._async_run(job, "t", "/media"))
        await asyncio.sleep(0)
        skipped = await mgr.async_upgrade_ytdlp(skip_if_busy=True)
        release.set()
        await download
        return skipped

    assert asyncio.run(run()) == ("1", "1")
    assert order == ["download start", "download done"]
    assert mgr.ytdlp_version == "1"


def test_version_is_not_read_on_the_event_loop(monkeypatch):
    monkeypatch.setattr(updater, "installed_version", lambda: pytest.fail("read on loop"))
    hass = _FakeHass()
    mgr = manager.DownloadManager(hass, types.SimpleNamespace(entry_id="e1", data={}, options={}))
    assert mgr.ytdlp_version is None


def test_first_upgrade_notifies_even_when_pip_changed_nothing(monkeypatch):
    # Sensor starts at None; a (x, x) result from pip must still re-render it.
    hass = _FakeHass()
    mgr = manager.DownloadManager(hass, types.SimpleNamespace(entry_id="e1", data={}, options={}))
    notified: list[object] = []
    mgr.async_add_listener(notified.append)
    monkeypatch.setattr(manager, "upgrade", lambda d=None: ("2026.8.19", "2026.8.19"))

    assert asyncio.run(mgr.async_upgrade_ytdlp()) == ("2026.8.19", "2026.8.19")
    assert mgr.ytdlp_version == "2026.8.19"
    assert notified == [None]


# -- title-based folder, clean URLs, auto-retry ----------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("10 Minute Morning Meditation", "meditations/morning"),
        ("Deep Sleep Body Scan", "meditations/sleep"),
        ("Good Night wind-down", "meditations/sleep"),
        ("Morning to night calm", "meditations/sleep"),
        ("Breathing basics", "meditations/morning"),  # preferred fallback
    ],
)
def test_suggest_folder_for_title(tmp_path, title, expected):
    _tree(tmp_path)
    folders = media_folders._scan({"local": str(tmp_path)})
    picked = media_folders.suggest_folder_for_title(folders, title, "meditations/morning")
    assert picked == str(tmp_path / expected)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://youtu.be/JdachuM5sYI?si=NEy2sR8h29sTWmiY", "https://www.youtube.com/watch?v=JdachuM5sYI"),
        ("https://www.youtube.com/watch?v=JdachuM5sYI&list=PLx&t=42s&si=abc", "https://www.youtube.com/watch?v=JdachuM5sYI"),
        ("https://m.youtube.com/shorts/JdachuM5sYI?feature=share", "https://www.youtube.com/watch?v=JdachuM5sYI"),
        ("https://www.youtube.com/live/JdachuM5sYI?si=x", "https://www.youtube.com/watch?v=JdachuM5sYI"),
        ("JdachuM5sYI", "https://www.youtube.com/watch?v=JdachuM5sYI"),
        ("https://www.youtube.com/playlist?list=PLabc&si=x", "https://www.youtube.com/playlist?list=PLabc"),
        ("https://example.com/a.jpg?x=1", "https://example.com/a.jpg?x=1"),
    ],
)
def test_clean_youtube_url(url, expected):
    assert youtube.normalize_youtube_ref(url) == expected


def test_any_failure_waits_then_retries_once(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(manager.asyncio, "sleep", fake_sleep)
    job, attempts, upgrades, _ = _run_download(monkeypatch, ["network blip"], ("1", "2"))
    assert (len(attempts), sleeps, job.state) == (2, [5], const.STATE_COMPLETED)


def test_auto_retry_happens_only_once(monkeypatch):
    async def fake_sleep(delay):
        pass

    monkeypatch.setattr(manager.asyncio, "sleep", fake_sleep)
    job, attempts, _, _ = _run_download(monkeypatch, ["a", "b", "c"], ("1", "2"))
    assert (len(attempts), job.state, job.error) == (2, const.STATE_FAILED, "b")
