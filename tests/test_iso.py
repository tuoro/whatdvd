from pathlib import Path

import pytest

from conftest import FakeRunner, make_file, ok
from whatdvd.dvd import ScanError
from whatdvd.iso import IsoEntry, open_iso, parse_listing, select_files
from whatdvd.runner import CommandResult

# 7-Zip 23.01 `7z l -slt` 对 genisoimage -dvd-video 生成的 ISO 的输出（删去了部分字段）
LISTING = """
7-Zip 23.01 (x64) : Copyright (c) 1999-2023 Igor Pavlov : 2023-06-20

Listing archive: /data/Disc 1.iso

--
Path = /data/Disc 1.iso
Type = Udf
Comment =
{
Primary Volumes:
  VolumeId: DISC1
}

----------
Path = AUDIO_TS
Folder = +
Size =
Packed Size =

Path = VIDEO_TS
Folder = +
Size =

Path = VIDEO_TS/VIDEO_TS.IFO
Folder = -
Size = 6144

Path = VIDEO_TS/VTS_01_0.IFO
Folder = -
Size = 12288

Path = VIDEO_TS/VTS_01_1.VOB
Folder = -
Size = 2131968

Path = VIDEO_TS/VTS_02_0.BUP
Folder = -
Size = 12288

Path = VIDEO_TS/VTS_02_0.IFO
Folder = -
Size = 12288

Path = VIDEO_TS/VTS_02_1.VOB
Folder = -
Size = 17725440
"""


def test_parse_listing_keeps_files_only() -> None:
    entries = parse_listing(LISTING)
    assert [e.path for e in entries] == [
        "VIDEO_TS/VIDEO_TS.IFO",
        "VIDEO_TS/VTS_01_0.IFO",
        "VIDEO_TS/VTS_01_1.VOB",
        "VIDEO_TS/VTS_02_0.BUP",
        "VIDEO_TS/VTS_02_0.IFO",
        "VIDEO_TS/VTS_02_1.VOB",
    ]
    assert entries[-1].size == 17725440


def test_parse_listing_without_separator() -> None:
    with pytest.raises(ScanError):
        parse_listing("Error: not an archive")


def test_select_files_by_ifo_duration() -> None:
    selection = select_files(parse_listing(LISTING), {"01": 8.0, "02": 70.0})
    assert selection.vob.path == "VIDEO_TS/VTS_02_1.VOB"
    assert selection.ifo is not None and selection.ifo.path == "VIDEO_TS/VTS_02_0.IFO"
    assert selection.title_set == "02"


def test_select_files_without_durations_uses_jietu_rules() -> None:
    selection = select_files(parse_listing(LISTING), {})
    assert selection.vob.path == "VIDEO_TS/VTS_02_1.VOB"
    # 两个 IFO 同大小，按名称取第一个：这是 jietu 规则的已知局限
    assert selection.ifo is not None and selection.ifo.path == "VIDEO_TS/VTS_01_0.IFO"


def test_select_files_ignores_files_outside_video_ts() -> None:
    entries = [IsoEntry("BONUS/big.VOB", 10**9), IsoEntry("VIDEO_TS/VTS_01_1.VOB", 10)]
    assert select_files(entries, {}).vob.path == "VIDEO_TS/VTS_01_1.VOB"


@pytest.mark.parametrize(
    "entries",
    [[], [IsoEntry("VIDEO_TS/VIDEO_TS.IFO", 99), IsoEntry("VIDEO_TS/VTS_01_1.VOB", 1)]],
)
def test_select_files_errors(entries: list[IsoEntry]) -> None:
    with pytest.raises(ScanError):
        select_files(entries, {})


IFO_DURATIONS = {"VTS_01_0.IFO": "8.000", "VTS_02_0.IFO": "70.000"}


def _fake_7z(argv: tuple[str, ...]) -> CommandResult:
    if argv[0] == "mediainfo":
        duration = IFO_DURATIONS[Path(argv[-1]).name]
        return ok(argv, f'{{"media": {{"track": [{{"@type": "General"}}, {{"Duration": "{duration}"}}]}}}}')
    if argv[1] == "l":
        return ok(argv, LISTING)
    target = Path(next(a for a in argv if a.startswith("-o"))[2:])
    iso_index = next(i for i, a in enumerate(argv) if a.endswith(".iso"))
    for inner in argv[iso_index + 1 :]:
        make_file(target / inner, 100)
    return ok(argv)


def test_open_iso_extracts_only_selected_files_and_cleans_up(tmp_path: Path) -> None:
    iso = make_file(tmp_path / "Disc 1.iso", 20_854_784)
    runner = FakeRunner(_fake_7z)
    with open_iso(runner, iso, temp_root=tmp_path) as disc:
        # 先解出各组 IFO 读时长，再只解正片的 VOB（IFO 已经解出，不重复解）
        ifos, vob = runner.calls[1], runner.calls[4]
        assert ifos[:5] == ("7z", "x", "-y", "-bso0", "-bsp0")
        assert ifos[-2:] == ("VIDEO_TS/VTS_01_0.IFO", "VIDEO_TS/VTS_02_0.IFO")
        assert [call[0] for call in runner.calls[2:4]] == ["mediainfo", "mediainfo"]
        assert vob[-2:] == (str(iso), "VIDEO_TS/VTS_02_1.VOB")
        assert len(runner.calls) == 5
        assert disc.name == "Disc 1"
        assert disc.title_set == "02"
        assert disc.vob.is_file() and disc.vob.name == "VTS_02_1.VOB"
        assert disc.ifo is not None and disc.ifo.name == "VTS_02_0.IFO"
        assert disc.total_bytes == 20_854_784
        assert disc.vob.relative_to(disc.mediainfo_root).as_posix() == "Disc 1/VIDEO_TS/VTS_02_1.VOB"
        temp = disc.mediainfo_root
    assert not temp.exists()


def test_open_iso_reports_missing_extraction(tmp_path: Path) -> None:
    iso = make_file(tmp_path / "Disc 1.iso", 1)
    runner = FakeRunner(lambda argv: ok(argv, LISTING if argv[1:2] == ("l",) else ""))
    with pytest.raises(ScanError, match="没有解出"), open_iso(runner, iso, temp_root=tmp_path):
        pass


def test_open_iso_rejects_unsafe_paths(tmp_path: Path) -> None:
    iso = make_file(tmp_path / "evil.iso", 1)
    listing = "----------\nPath = ../../VIDEO_TS/VTS_01_1.VOB\nFolder = -\nSize = 5\n"
    runner = FakeRunner(lambda argv: ok(argv, listing))
    with pytest.raises(ScanError, match="不安全"), open_iso(runner, iso, temp_root=tmp_path):
        pass


def test_open_iso_creates_missing_temp_root(tmp_path: Path) -> None:
    iso = make_file(tmp_path / "Disc 1.iso", 1)
    temp_root = tmp_path / "output" / ".tmp"
    with open_iso(FakeRunner(_fake_7z), iso, temp_root=temp_root) as disc:
        assert disc.vob.is_relative_to(temp_root.resolve())
    assert temp_root.is_dir() and not any(temp_root.iterdir())
