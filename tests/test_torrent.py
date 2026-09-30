from pathlib import Path

import pytest

from conftest import FakeRunner
from whatdvd.torrent import make_torrent, torrent_name


def test_torrent_name_like_zuozhong() -> None:
    assert torrent_name(Path("/dl/Movie Name (2001)")) == "Movie.Name.2001.torrent"
    assert torrent_name(Path("/dl/Movie.Name.2001.iso")) == "Movie.Name.2001.iso.torrent"


def test_make_torrent_args_match_zuozhong(tmp_path: Path) -> None:
    runner = FakeRunner()
    stale = tmp_path / "out" / "Movie.torrent"
    stale.parent.mkdir()
    stale.write_bytes(b"old")
    output = make_torrent(runner, Path("/dl/Movie"), tmp_path / "out", announces=["https://t.example/a"])
    assert output == stale
    assert not stale.exists()  # 旧文件先删除，mktorrent 不会覆盖
    assert runner.calls == [
        ("mktorrent", "-v", "-p", "-l", "24", "-a", "https://t.example/a", "-o", str(stale), "/dl/Movie"),
    ]


def test_make_torrent_without_announce(tmp_path: Path) -> None:
    runner = FakeRunner()
    make_torrent(runner, Path("/dl/Movie"), tmp_path, piece_length=22)
    assert "-a" not in runner.calls[0]
    assert runner.calls[0][3:5] == ("-l", "22")


@pytest.mark.parametrize("piece_length", [14, 29])
def test_make_torrent_rejects_out_of_range_piece_length(tmp_path: Path, piece_length: int) -> None:
    with pytest.raises(ValueError):
        make_torrent(FakeRunner(), Path("/dl/Movie"), tmp_path, piece_length=piece_length)
