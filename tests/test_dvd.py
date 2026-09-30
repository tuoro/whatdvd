from pathlib import Path

import pytest

from conftest import FakeRunner, make_file, ok
from whatdvd.dvd import (
    DVD5_MAX_BYTES,
    ScanError,
    filter_fake_sets,
    largest_file,
    pick_largest,
    pick_main_set,
    scan_disc,
    select_title,
    tv_standard,
    vts_part,
)
from whatdvd.runner import CommandResult

GB_PART = 1_073_709_056  # DVD 标题 VOB 分段的典型大小


def make_video_ts(root: Path, files: dict[str, int]) -> Path:
    video_ts = root / "VIDEO_TS"
    for name, size in files.items():
        make_file(video_ts / name, size)
    return video_ts


def test_without_ifo_durations_picks_largest_vob_like_jietu(tmp_path: Path) -> None:
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
    disc = scan_disc(FakeRunner(), video_ts, Path("/srv"))
    assert disc.name == "Disc"
    assert disc.mediainfo_root == Path("/srv")
    assert disc.vob.name == "VTS_02_1.VOB"
    assert disc.ifo is not None and disc.ifo.name == "VTS_02_0.IFO"


def test_without_durations_equal_sizes_resolve_by_name_like_ls_s(tmp_path: Path) -> None:
    # jietu 的已知局限：另一组也有 1GB 分段且编号更小时，选中的是那一组。
    video_ts = make_video_ts(
        tmp_path / "Disc",
        {"VTS_01_1.VOB": GB_PART, "VTS_02_1.VOB": GB_PART, "VTS_02_2.VOB": GB_PART},
    )
    assert scan_disc(FakeRunner(), video_ts, Path("/")).vob.name == "VTS_01_1.VOB"


def test_without_durations_ifo_is_largest_ifo_and_ignores_bup(tmp_path: Path) -> None:
    video_ts = make_video_ts(
        tmp_path / "Disc",
        {"VTS_01_1.VOB": 1000, "VTS_01_0.IFO": 100, "VTS_01_0.BUP": 999, "VIDEO_TS.IFO": 200},
    )
    disc = scan_disc(FakeRunner(), video_ts, Path("/"))
    assert disc.ifo is not None and disc.ifo.name == "VIDEO_TS.IFO"


def test_lowercase_names(tmp_path: Path) -> None:
    video_ts = tmp_path / "disc" / "video_ts"
    make_file(video_ts / "vts_01_1.vob", 1000)
    make_file(video_ts / "vts_01_0.ifo", 10)
    disc = scan_disc(FakeRunner(), video_ts, Path("/"))
    assert disc.vob.name == "vts_01_1.vob"
    assert disc.ifo is not None


def test_largest_file_not_vob_is_error(tmp_path: Path) -> None:
    video_ts = make_video_ts(tmp_path / "Disc", {"VIDEO_TS.IFO": 5000, "VTS_01_1.VOB": 10})
    with pytest.raises(ScanError, match="不是 VOB"):
        scan_disc(FakeRunner(), video_ts, Path("/"))


def test_empty_video_ts_is_error(tmp_path: Path) -> None:
    video_ts = tmp_path / "Disc" / "VIDEO_TS"
    video_ts.mkdir(parents=True)
    with pytest.raises(ScanError, match="空"):
        scan_disc(FakeRunner(), video_ts, Path("/"))


def test_media_type_boundary(tmp_path: Path) -> None:
    dvd5 = scan_disc(FakeRunner(), make_video_ts(tmp_path / "A", {"VTS_01_1.VOB": DVD5_MAX_BYTES}), Path("/"))
    dvd9 = scan_disc(FakeRunner(), make_video_ts(tmp_path / "B", {"VTS_01_1.VOB": DVD5_MAX_BYTES + 1}), Path("/"))
    assert dvd5.media_type == "DVD5"
    assert dvd9.media_type == "DVD9"


def test_file_title_uses_disc_folder_and_vob_name(tmp_path: Path) -> None:
    video_ts = make_video_ts(tmp_path / "Movie Name (2001)", {"VTS_01_1.VOB": 10})
    assert scan_disc(FakeRunner(), video_ts, Path("/")).file_title == "Movie.Name.2001.VTS_01_1.VOB"


def test_largest_file_empty() -> None:
    assert largest_file([]) is None


def test_pick_largest_generic() -> None:
    items = [("b", 5), ("a", 5), ("c", 1)]
    assert pick_largest(items, lambda i: i[1], lambda i: i[0]) == ("a", 5)


@pytest.mark.parametrize(("height", "expected"), [(576, "PAL"), (480, "NTSC"), (1080, None)])
def test_tv_standard(height: int, expected: str | None) -> None:
    assert tv_standard(height) == expected


def fake_mediainfo(durations: dict[str, str]) -> FakeRunner:
    """mediainfo --Output=JSON：按 IFO 文件名返回时长（秒）。"""

    def handler(argv: tuple[str, ...]) -> CommandResult:
        duration = durations.get(Path(argv[-1]).name)
        if duration is None:
            return ok(argv, "")
        return ok(argv, f'{{"media": {{"track": [{{"@type": "General", "Duration": "{duration}"}}, '
                        f'{{"@type": "Video", "Duration": "{duration}"}}]}}}}')

    return FakeRunner(handler)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("VTS_02_1.VOB", ("02", 1, "VOB")),
        ("vts_10_0.ifo", ("10", 0, "IFO")),
        ("VTS_01_0.BUP", None),
        ("VIDEO_TS.IFO", None),
        ("VTS_1_1.VOB", None),
    ],
)
def test_vts_part(name: str, expected: tuple[str, int, str] | None) -> None:
    assert vts_part(name) == expected


@pytest.mark.parametrize(
    ("durations", "expected"),
    [
        ({"01": 600.0, "02": 6000.0}, "02"),
        ({"01": 6000.0, "02": 6500.0}, "01"),  # 只长 8%，不替换：剧集盘选第一集
        ({"01": 6000.0, "02": 6700.0}, "02"),  # 长 10% 以上才替换
        ({"01": 0.0, "02": 30.0}, "02"),  # 时长无效的组跳过
        ({}, None),
    ],
)
def test_pick_main_set(durations: dict[str, float], expected: str | None) -> None:
    assert pick_main_set(durations) == expected


def test_main_set_by_ifo_duration_even_when_other_set_has_larger_file(tmp_path: Path) -> None:
    # 花絮组 VTS_01 有一个比正片分段更大的 VOB，按 jietu 会选错
    video_ts = make_video_ts(
        tmp_path / "Disc",
        {
            "VIDEO_TS.IFO": 12_288,
            "VTS_01_0.IFO": 90_112,
            "VTS_01_1.VOB": GB_PART,
            "VTS_02_0.IFO": 20_480,
            "VTS_02_0.VOB": 900_000_000,  # 菜单，不参与
            "VTS_02_1.VOB": 1_000_000_000,
            "VTS_02_2.VOB": 1_000_000_000,
            "VTS_02_3.VOB": 400_000_000,
        },
    )
    runner = fake_mediainfo({"VTS_01_0.IFO": "1500.000", "VTS_02_0.IFO": "6123.456"})
    disc = scan_disc(runner, video_ts, Path("/"))
    assert disc.title_set == "02" and disc.title_duration == 6123.456
    assert disc.vob.name == "VTS_02_1.VOB"  # 组内同样大小按文件名取第一个
    assert disc.ifo is not None and disc.ifo.name == "VTS_02_0.IFO"
    assert sorted(call[-1].rsplit("/", 1)[-1] for call in runner.calls) == ["VTS_01_0.IFO", "VTS_02_0.IFO"]


def test_main_set_without_title_vob_falls_back(tmp_path: Path) -> None:
    video_ts = make_video_ts(tmp_path / "Disc", {"VTS_01_0.IFO": 100, "VTS_01_0.VOB": 5000, "VTS_02_1.VOB": 10})
    disc = scan_disc(fake_mediainfo({"VTS_01_0.IFO": "100"}), video_ts, Path("/"))
    assert disc.title_set is None and disc.vob.name == "VTS_01_0.VOB"


def test_select_title_generic() -> None:
    files = [("VTS_01_1.VOB", 9_000_000), ("VTS_02_1.VOB", 5_000_000), ("VTS_02_0.IFO", 1)]
    selection = select_title(files, lambda f: f[0], lambda f: f[1], {"02": 10.0})  # 4 Mbps
    assert selection.vob == ("VTS_02_1.VOB", 5_000_000) and selection.ifo == ("VTS_02_0.IFO", 1)
    fallback = select_title(files, lambda f: f[0], lambda f: f[1], {})
    assert fallback.vob == ("VTS_01_1.VOB", 9_000_000) and fallback.title_set is None


def test_fake_title_with_long_duration_but_small_vob_is_skipped(tmp_path: Path) -> None:
    # 复制保护盘：VTS_03 在 IFO 中写了 9 小时，VOB 却只有 120 MB（约 0.03 Mbps）
    video_ts = make_video_ts(
        tmp_path / "Disc",
        {
            "VTS_01_0.IFO": 20_480,
            "VTS_01_1.VOB": GB_PART,
            "VTS_01_2.VOB": GB_PART,
            "VTS_01_3.VOB": 800_000_000,
            "VTS_02_0.IFO": 20_480,
            "VTS_02_1.VOB": 300_000_000,
            "VTS_03_0.IFO": 20_480,
            "VTS_03_1.VOB": 120_000_000,
        },
    )
    runner = fake_mediainfo({"VTS_01_0.IFO": "6300", "VTS_02_0.IFO": "900", "VTS_03_0.IFO": "32400"})
    disc = scan_disc(runner, video_ts, Path("/"))
    assert disc.title_set == "01"
    assert disc.vob.name == "VTS_01_1.VOB"
    assert [s.title_set for s in disc.skipped_sets] == ["03"]
    assert disc.skipped_sets[0].vob_bytes == 120_000_000


@pytest.mark.parametrize(
    ("duration", "vob_bytes", "skipped"),
    [
        (3600.0, 225_000_000, False),  # 恰好 0.5 Mbps
        (3600.0, 224_999_999, True),
        (7200.0, 4_000_000_000, False),  # 普通电影，约 4.4 Mbps
        (100.0, 0, True),  # 只有菜单 VOB
    ],
)
def test_filter_fake_sets_threshold(duration: float, vob_bytes: int, skipped: bool) -> None:
    kept, dropped = filter_fake_sets({"01": duration}, {"01": vob_bytes} if vob_bytes else {})
    assert (kept == {}) is skipped
    assert bool(dropped) is skipped


def test_all_sets_fake_falls_back_to_largest_file(tmp_path: Path) -> None:
    video_ts = make_video_ts(tmp_path / "Disc", {"VTS_01_0.IFO": 100, "VTS_01_1.VOB": 1000, "VTS_02_1.VOB": 2000})
    disc = scan_disc(fake_mediainfo({"VTS_01_0.IFO": "36000"}), video_ts, Path("/"))
    assert disc.title_set is None and disc.vob.name == "VTS_02_1.VOB"
    assert [s.title_set for s in disc.skipped_sets] == ["01"]


def test_title_ifo_only_when_same_set(tmp_path: Path) -> None:
    video_ts = make_video_ts(tmp_path / "Disc", {"VTS_02_0.IFO": 100, "VTS_02_1.VOB": 10_000_000})
    disc = scan_disc(fake_mediainfo({"VTS_02_0.IFO": "10"}), video_ts, Path("/"))
    assert disc.title_ifo is not None and disc.title_ifo.name == "VTS_02_0.IFO"
    # 退回 jietu 规则时 IFO 可能是 VIDEO_TS.IFO，不能用来读比例
    other = make_video_ts(tmp_path / "Other", {"VIDEO_TS.IFO": 5000, "VTS_01_1.VOB": 10_000})
    assert scan_disc(FakeRunner(), other, Path("/")).title_ifo is None
