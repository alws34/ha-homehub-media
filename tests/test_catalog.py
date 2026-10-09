import os
from pathlib import Path

import pytest

from custom_components.homehub_media import catalog as c


def test_slugify():
    assert c.slugify("Cozy Fireplace 4K") == "cozy_fireplace_4k"
    assert c.slugify("!!!") == "media"
    assert len(c.slugify("a" * 200)) == 64


def test_scan_ids_collisions_previews_and_escape(tmp_path: Path):
    root = tmp_path / "videos"; (root / "sub").mkdir(parents=True)
    (root / "Rain.mp4").write_bytes(b"x"); (root / "sub" / "rain.mkv").write_bytes(b"x")
    (root / "Rain.webp").write_bytes(b"x"); (root / "notes.txt").write_text("x")
    outside = tmp_path / "secret.mp4"; outside.write_bytes(b"x")
    os.symlink(outside, root / "evil.mp4")
    items = c.scan(root, "visual")
    assert [i["id"] for i in items] == ["rain", "rain_2"]
    assert items[0]["preview_path"] == "Rain.webp" and items[0]["title"] == "Rain"
    assert all("evil" not in i["path"] for i in items)


def test_scan_skips_hidden_and_matches_preview_case_insensitively(tmp_path: Path):
    root = tmp_path / "videos"; (root / ".trash").mkdir(parents=True)
    (root / "Rain.mp4").write_bytes(b"x"); (root / "._Rain.mp4").write_bytes(b"x")
    (root / ".trash" / "Gone.mp4").write_bytes(b"x"); (root / "Rain.JPG").write_bytes(b"x")
    items = c.scan(root, "visual")
    assert [(i["id"], i["path"], i["preview_path"]) for i in items] == [("rain", "Rain.mp4", "Rain.JPG")]


def test_radio_items_ok_and_normalised():
    [item] = c.radio_items({"radio_jazz_fm": " HTTPS://x/a\r"})
    assert item == {
        "id": "radio_jazz_fm", "kind": "soundtrack", "title": "Jazz Fm Live", "source_type": "stream",
        "live": True, "duration": None, "url": "https://x/a",
    }


@pytest.mark.parametrize("url", ["http://x/a", "https://u:p@x/a", "https://x/a#f", "file:///etc/passwd"])
def test_radio_rejects_bad_urls(url):
    with pytest.raises(ValueError):
        c.radio_items({"radio_x": url})


def test_composition_defaults_and_strictness():
    comp = c.validate_composition({"visual": "rain"})
    assert comp["visual_enabled"] is True and comp["original_volume"] == 20 and comp["soundtrack_volume"] == 100
    with pytest.raises(ValueError):
        c.validate_composition({"original_volume": 101})
    with pytest.raises(ValueError):
        c.validate_composition({"video_loop": "yes"})
    with pytest.raises(ValueError):
        c.validate_composition({"bogus": 1})
    assert c.validate_composition({"soundtrack_volume": 50}, partial=True) == {"soundtrack_volume": 50}


def test_composition_volume_floats_are_rounded_to_int():
    for given, stored in ((50.0, 50), (33.4, 33), (0, 0), (100.0, 100)):
        vol = c.validate_composition({"original_volume": given}, partial=True)["original_volume"]
        assert vol == stored and type(vol) is int
    for bad in (True, float("nan"), float("inf"), -0.1, 100.5, "50", None):
        with pytest.raises(ValueError):
            c.validate_composition({"original_volume": bad})
