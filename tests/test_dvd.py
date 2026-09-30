from pathlib import Path

import pytest

from conftest import make_file
from whatdvd.dvd import DVD5_MAX_BYTES, ScanError, largest_file, pick_largest, scan_disc, tv_standard

GB_PART = 1_073_709_056  # DVD 标题 VOB 分段的典型大小


def make_video_ts(root: Path, files: dict[str, int]) -> Path:
    video_ts = root / "VIDEO_TS"
    for name, size in files.items():
        make_file(video_ts / name, size)
    return video_ts


def test_picks_largest_vob_even_when_main_is_not_vts_01(tmp_path: Path) -> None:
    video_ts = make_video_ts(
        tmp_path / "Disc",
        {
            "VIDEO_TS.IFO": 12_288,
            "VTS_01_0.IFO": 20_480,
            "VTS_01_1.VOB": 300_000_000,
            "VTS_02_0.IFO": 90_112,
            "VTS_02_1.VOB": GB_PART,
            "VTS_02_2.VOB": 500_000_000,
        },
    )
    disc = scan_disc(video_ts, "/srv/")
    assert disc.name == "Disc"
    assert disc.strip_prefix == "/srv/"
    assert disc.vob.name == "VTS_02_1.VOB"
    assert disc.ifo is not None and disc.ifo.name == "VTS_02_0.IFO"


def test_equal_sizes_resolve_by_name_like_ls_s(tmp_path: Path) -> None:
    # jietu 的已知局限：另一组也有 1GB 分段且编号更小时，选中的是那一组。
    video_ts = make_video_ts(
        tmp_path / "Disc",
        {"VTS_01_1.VOB": GB_PART, "VTS_02_1.VOB": GB_PART, "VTS_02_2.VOB": GB_PART},
    )
    assert scan_disc(video_ts, "/").vob.name == "VTS_01_1.VOB"


def test_ifo_is_largest_ifo_on_disc_and_ignores_bup(tmp_path: Path) -> None:
    video_ts = make_video_ts(
        tmp_path / "Disc",
        {"VTS_01_1.VOB": 1000, "VTS_01_0.IFO": 100, "VTS_01_0.BUP": 999, "VIDEO_TS.IFO": 200},
    )
    disc = scan_disc(video_ts, "/")
    assert disc.ifo is not None and disc.ifo.name == "VIDEO_TS.IFO"


def test_lowercase_names(tmp_path: Path) -> None:
    video_ts = tmp_path / "disc" / "video_ts"
    make_file(video_ts / "vts_01_1.vob", 1000)
    make_file(video_ts / "vts_01_0.ifo", 10)
    disc = scan_disc(video_ts, "/")
    assert disc.vob.name == "vts_01_1.vob"
    assert disc.ifo is not None


def test_largest_file_not_vob_is_error(tmp_path: Path) -> None:
    video_ts = make_video_ts(tmp_path / "Disc", {"VIDEO_TS.IFO": 5000, "VTS_01_1.VOB": 10})
    with pytest.raises(ScanError, match="不是 VOB"):
        scan_disc(video_ts, "/")


def test_empty_video_ts_is_error(tmp_path: Path) -> None:
    video_ts = tmp_path / "Disc" / "VIDEO_TS"
    video_ts.mkdir(parents=True)
    with pytest.raises(ScanError, match="空"):
        scan_disc(video_ts, "/")


def test_media_type_boundary(tmp_path: Path) -> None:
    dvd5 = scan_disc(make_video_ts(tmp_path / "A", {"VTS_01_1.VOB": DVD5_MAX_BYTES}), "/")
    dvd9 = scan_disc(make_video_ts(tmp_path / "B", {"VTS_01_1.VOB": DVD5_MAX_BYTES + 1}), "/")
    assert dvd5.media_type == "DVD5"
    assert dvd9.media_type == "DVD9"


def test_file_title_uses_disc_folder_and_vob_name(tmp_path: Path) -> None:
    video_ts = make_video_ts(tmp_path / "Movie Name (2001)", {"VTS_01_1.VOB": 10})
    assert scan_disc(video_ts, "/").file_title == "Movie.Name.2001.VTS_01_1.VOB"


def test_largest_file_empty() -> None:
    assert largest_file([]) is None


def test_pick_largest_generic() -> None:
    items = [("b", 5), ("a", 5), ("c", 1)]
    assert pick_largest(items, lambda i: i[1], lambda i: i[0]) == ("a", 5)


@pytest.mark.parametrize(("height", "expected"), [(576, "PAL"), (480, "NTSC"), (1080, None)])
def test_tv_standard(height: int, expected: str | None) -> None:
    assert tv_standard(height) == expected
