"""Tests for the pure logic in the YouTube Download integration.

These deliberately avoid importing Home Assistant: the modules under test are
the ones that only touch the filesystem and strings, so they run in plain
pytest with nothing installed.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
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


const = _load("const")
filenames = _load("filenames")
media_folders = _load("media_folders")


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
    """Build a small media tree with the shapes the scanner must handle."""
    (root / "meditations").mkdir()
    (root / "meditations" / "morning").mkdir()
    (root / "meditations" / "morning" / "short").mkdir()
    (root / "photos").mkdir()
    (root / ".hidden").mkdir()
    (root / "@eaDir").mkdir()
    (root / "photos" / "note.txt").write_text("not a folder")


def test_scan_is_fully_recursive_and_skips_bookkeeping(tmp_path):
    _tree(tmp_path)
    folders = media_folders._scan({"media": str(tmp_path)})
    labels = [folder["label"] for folder in folders]

    assert labels[0] == "media"  # the source root itself
    assert "meditations" in labels
    assert os.path.join("meditations", "morning") in labels
    assert os.path.join("meditations", "morning", "short") in labels
    assert "photos" in labels
    assert ".hidden" not in labels
    assert "@eaDir" not in labels


def test_scan_survives_a_symlink_loop(tmp_path):
    _tree(tmp_path)
    (tmp_path / "meditations" / "loop").symlink_to(tmp_path, target_is_directory=True)

    folders = media_folders._scan({"media": str(tmp_path)})

    assert len(folders) < 20  # would not terminate without loop protection
    assert any(folder["label"] == "meditations" for folder in folders)


def test_scan_ignores_a_missing_source(tmp_path):
    assert media_folders._scan({"media": str(tmp_path / "nope")}) == []


def test_preferred_media_folder_matches_case_insensitively(tmp_path):
    _tree(tmp_path)
    folders = media_folders._scan({"media": str(tmp_path)})

    picked = media_folders.preferred_media_folder(folders, "MEDITATION")
    assert picked == str(tmp_path / "meditations")


def test_preferred_media_folder_returns_none_without_a_match(tmp_path):
    _tree(tmp_path)
    folders = media_folders._scan({"media": str(tmp_path)})

    assert media_folders.preferred_media_folder(folders, "podcasts") is None
    # Blank means "always choose manually", which images rely on.
    assert media_folders.preferred_media_folder(folders, "") is None


# -- constants ------------------------------------------------------------


def test_image_suffixes_cover_the_content_types():
    for extension in const.IMAGE_EXTENSIONS.values():
        assert extension in const.IMAGE_SUFFIXES


def test_youtube_hosts_are_lowercase():
    assert all(host == host.lower() for host in const.YOUTUBE_HOSTS)
