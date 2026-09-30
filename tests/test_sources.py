from pathlib import Path

import pytest

from conftest import make_file
from whatdvd.dvd import ScanError
from whatdvd.sources import find_sources, open_disc


def test_find_sources_accepts_video_ts_disc_root_and_multi_disc(tmp_path: Path) -> None:
    disc1 = make_file(tmp_path / "Movie" / "Disc 1" / "VIDEO_TS" / "VTS_01_1.VOB", 10).parent
    disc2 = make_file(tmp_path / "Movie" / "Disc 2" / "VIDEO_TS" / "VTS_01_1.VOB", 10).parent
    assert find_sources(disc1) == [disc1]
    assert find_sources(tmp_path / "Movie" / "Disc 1") == [disc1]
    assert find_sources(tmp_path / "Movie") == [disc1, disc2]


def test_find_sources_finds_iso_files(tmp_path: Path) -> None:
    iso1 = make_file(tmp_path / "Movie" / "Movie.D1.iso", 10)
    iso2 = make_file(tmp_path / "Movie" / "Movie.D2.ISO", 10)
    make_file(tmp_path / "Movie" / "notes.txt", 10)
    assert find_sources(tmp_path / "Movie") == [iso1, iso2]
    assert find_sources(iso1) == [iso1]


def test_find_sources_errors(tmp_path: Path) -> None:
    with pytest.raises(ScanError, match="没有找到"):
        find_sources(tmp_path)
    with pytest.raises(ScanError, match="不是 ISO"):
        find_sources(make_file(tmp_path / "a.mkv", 1))
    with pytest.raises(ScanError, match="不存在"):
        find_sources(tmp_path / "missing")


def test_open_disc_folder_uses_input_parent(tmp_path: Path, fake_runner: type) -> None:
    video_ts = make_file(tmp_path / "Movie" / "Disc 1" / "VIDEO_TS" / "VTS_01_1.VOB", 10).parent
    with open_disc(fake_runner(), video_ts, tmp_path / "Movie") as disc:
        assert disc.mediainfo_root == tmp_path
        assert disc.name == "Disc 1"
