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


VID = "B5unCXpegAw"


@pytest.mark.parametrize("value", [
    "https://www.youtube.com/watch?v=B5unCXpegAw&pp=ygUKc3RhciBuaWdodA%3D%3D",  # the owner's real link
    "https://youtube.com/watch?feature=share&v=B5unCXpegAw",
    "http://m.youtube.com/watch?v=B5unCXpegAw&t=42s",
    "https://music.youtube.com/watch?v=B5unCXpegAw&list=RDAMVM",
    "https://youtu.be/B5unCXpegAw?si=Xy_12-ab",
    "youtu.be/B5unCXpegAw",
    "www.youtube.com/watch?v=B5unCXpegAw",
    "https://www.youtube.com/shorts/B5unCXpegAw",
    "https://www.youtube.com/live/B5unCXpegAw?feature=shared",
    "  B5unCXpegAw\n",
])
def test_youtube_id_parses_links(value):
    assert c.youtube_id(value) == VID


@pytest.mark.parametrize("value", [
    "", "B5unCXpegA", "B5unCXpegAw1", "B5unCXpeg/w", None, 11,
    "https://evil.example/watch?v=B5unCXpegAw", "https://www.youtube.com.evil.example/watch?v=B5unCXpegAw",
    "https://www.youtube.com/watch?v=short", "https://www.youtube.com/channel/UCabcdefghij",
    "https://www.youtube.com/watch", "https://youtu.be/", "javascript:alert(1)", "ftp://youtu.be/B5unCXpegAw",
])
def test_youtube_id_rejects(value):
    with pytest.raises(ValueError):
        c.youtube_id(value)


def test_youtube_items_keep_order_and_unique_ids():
    entries = {f"Video {i:02d}": f"https://youtu.be/{VID[:-2]}{i:02d}" for i in range(25, 0, -1)}
    entries["Rain"] = f"https://www.youtube.com/watch?v={VID}&pp=x"
    entries["rain!"] = VID
    items = c.youtube_items(entries, taken={"video_25"})
    assert [i["title"] for i in items] == list(entries)  # the user's order, 20+ lines
    assert [i["id"] for i in items[:2]] == ["video_25_2", "video_24"]  # never a scanned file's id
    assert [i["id"] for i in items[-2:]] == ["rain", "rain_2"]
    assert items[-2] == {"id": "rain", "kind": "visual", "title": "Rain", "source_type": "youtube", "live": False,
                         "duration": None, "youtube_id": VID, "preview": f"/api/homehub_media/art/youtube/{VID}",
                         "link": f"https://www.youtube.com/watch?v={VID}&pp=x"}
    for bad in ({" ": VID}, {"Rain": "https://example.com/x"}):
        with pytest.raises(ValueError):
            c.youtube_items(bad)


def test_youtube_ids_for_names_without_latin_letters_come_from_the_video():
    sunset, fire = {"שקיעה": VID}, {"🔥🔥": "fireFIREfir"}
    assert [i["id"] for i in c.youtube_items({**sunset, **fire})] == ["yt_b5uncxpegaw", "yt_firefirefir"]
    assert [i["id"] for i in c.youtube_items({**fire, **sunset})] == ["yt_firefirefir", "yt_b5uncxpegaw"]
    item = c.youtube_items(sunset)[0]
    assert item["title"] == "שקיעה" and c.ID_RE.fullmatch(item["id"])
    assert c.youtube_items({"🔥 Fire": VID})[0]["id"] == "fire"  # Latin letters still name it


def test_composition_visuals_pool():
    comp = c.validate_composition({"visuals": ["rain", "fire", "rain"]})
    assert comp["visuals"] == ["rain", "fire"] and comp["visual"] is None and comp["visual_enabled"] is True
    assert c.validate_composition({"visual": "rain"})["visuals"] == []
    assert c.validate_composition({})["visuals"] is not c.validate_composition({})["visuals"]  # no shared default
    with pytest.raises(ValueError):  # enabled with neither a visual nor a pool
        c.validate_composition({"visual_enabled": True, "visuals": []})
    for bad in ("rain", ["Rain"], ["../x"], [None], [f"v{i}" for i in range(51)]):
        with pytest.raises(ValueError):
            c.validate_composition({"visuals": bad})
    assert c.validate_composition({"visuals": [f"v{i}" for i in range(50)]}, partial=True)["visuals"][-1] == "v49"
