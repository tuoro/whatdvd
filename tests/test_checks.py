from pathlib import Path

import pytest

from conftest import make_file
from whatdvd.checks import describe_extra_files, find_extra_files


def _disc(root: Path) -> Path:
    for name in ("VIDEO_TS.IFO", "VIDEO_TS.BUP", "VTS_01_0.IFO", "VTS_01_0.BUP", "VTS_01_1.VOB"):
        make_file(root / "Disc 1" / "VIDEO_TS" / name, 1)
    (root / "Disc 1" / "AUDIO_TS").mkdir()
    return root


def test_clean_disc_has_no_extra_files(tmp_path: Path) -> None:
    movie = _disc(tmp_path / "Movie")
    make_file(movie / "Movie.nfo", 1)  # 抓盘相关的文件可以保留
    make_file(movie / "Disc 1" / "rip.log", 1)
    make_file(movie / "Disc 1" / "JACKET_P" / "J00___5L.MP2", 1)  # DVD 结构的一部分
    assert find_extra_files(movie) == []


@pytest.mark.parametrize(
    ("relative", "reason"),
    [
        ("Thumbs.db", "系统生成的文件"),
        ("Disc 1/desktop.ini", "系统生成的文件"),
        (".DS_Store", "系统生成的文件"),
        ("Disc 1/VIDEO_TS/._VTS_01_1.VOB", "系统生成的文件"),
        ("Disc 1/VIDEO_TS.VOB.scr01.png", "图片（截图或封面）"),
        ("cover.JPG", "图片（截图或封面）"),
        ("Movie.sample.mkv", "视频文件（样片？）"),
        ("sample.vob", "视频文件（样片？）"),
        ("Movie.torrent", "种子或发布用的文件"),
        ("Disc.1.VTS_01_1.VOB.mediainfo.txt", "种子或发布用的文件"),
        ("Disc 1/VIDEO_TS/VTS_01_2.VOB.!qB", "没下载完的文件"),
        ("IMDb.url", "快捷方式"),
        ("Disc 1/VIDEO_TS/readme.txt", "VIDEO_TS 中不属于 DVD 结构的文件"),
    ],
)
def test_extra_files_are_reported(tmp_path: Path, relative: str, reason: str) -> None:
    movie = _disc(tmp_path / "Movie")
    make_file(movie / relative, 1)
    found = find_extra_files(movie)
    assert [(f.path.as_posix(), f.reason) for f in found] == [(relative, reason)]


def test_system_directories_are_reported_once(tmp_path: Path) -> None:
    movie = _disc(tmp_path / "Movie")
    make_file(movie / "@eaDir" / "Thumbs.db" / "SYNOINDEX_MEDIA_INFO", 1)
    make_file(movie / "Disc 1" / "__MACOSX" / "x", 1)
    found = find_extra_files(movie)
    assert [(f.path.as_posix(), f.reason) for f in found] == [
        ("@eaDir", "系统生成的目录"),
        ("Disc 1/__MACOSX", "系统生成的目录"),
    ]


def test_single_file_is_not_checked(tmp_path: Path) -> None:
    assert find_extra_files(make_file(tmp_path / "Disc.iso", 1)) == []


def test_describe_limits_output(tmp_path: Path) -> None:
    movie = _disc(tmp_path / "Movie")
    for i in range(25):
        make_file(movie / f"shot{i:02d}.png", 1)
    lines = describe_extra_files(find_extra_files(movie), limit=20)
    assert lines[0] == "注意：发现 25 个和上传无关的文件，建议删除后再做种（PTP 2.1.3）："
    assert lines[1] == "  shot00.png（图片（截图或封面））"
    assert lines[-1] == "  ……另有 5 个"
    assert describe_extra_files([]) == []
