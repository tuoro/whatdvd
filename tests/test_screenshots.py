from pathlib import Path

import pytest

from conftest import FakeRunner, ok
from whatdvd.runner import CommandResult
from whatdvd.screenshots import capture, compress, format_timestamp, step_seconds, timestamps


@pytest.mark.parametrize(
    ("duration", "step"),
    [(0, 21), (599, 21), (600, 71), (1499, 71), (1500, 121), (3599, 121), (3600, 331), (9000, 331)],
)
def test_step_buckets(duration: int, step: int) -> None:
    assert step_seconds(duration) == step


def test_timestamps_start_at_one_step() -> None:
    assert timestamps(1800, 3) == [121, 242, 363]
    assert timestamps(1800, 10)[-1] == 1210


@pytest.mark.parametrize(("seconds", "text"), [(21, "00:00:21"), (3310, "00:55:10"), (86400 + 5, "00:00:05")])
def test_format_timestamp(seconds: int, text: str) -> None:
    assert format_timestamp(seconds) == text


def _ffmpeg_writes_output(argv: tuple[str, ...]) -> CommandResult:
    Path(argv[-1]).write_bytes(b"png")
    return ok(argv)


def test_capture_command_matches_jietu(tmp_path: Path) -> None:
    runner = FakeRunner(_ffmpeg_writes_output)
    out = tmp_path / "a.scr01.png"
    assert capture(runner, Path("/d/VTS_01_1.VOB"), 121, (1024, 576), out)
    argv = runner.calls[0]
    assert argv[0] == "ffmpeg"
    # 输入端 -ss 在 -i 之前，输出端 -ss 00:00:01 在 -i 之后
    i = argv.index("-i")
    assert argv[i - 2 : i] == ("-ss", "00:02:01")
    assert argv[i + 1] == "/d/VTS_01_1.VOB"
    assert argv[i + 2 : i + 4] == ("-ss", "00:00:01")
    assert ("-frames:v", "1") == argv[argv.index("-frames:v") : argv.index("-frames:v") + 2]
    assert argv[argv.index("-s") + 1] == "1024x576"
    # 除了缩放，不加任何滤镜
    assert "-vf" not in argv and "-filter:v" not in argv


def test_capture_fails_when_no_file_and_removes_stale_output(tmp_path: Path) -> None:
    out = tmp_path / "a.scr01.png"
    out.write_bytes(b"old")
    runner = FakeRunner(lambda argv: CommandResult(argv, 1, "", "error"))
    assert not capture(runner, Path("x.VOB"), 21, (720, 540), out)
    assert not out.exists()


def test_compress_replaces_original(tmp_path: Path) -> None:
    png = tmp_path / "a.scr01.png"
    png.write_bytes(b"big")

    def nconvert(argv: tuple[str, ...]) -> CommandResult:
        Path(argv[argv.index("-o") + 1]).write_bytes(b"small")
        return ok(argv)

    runner = FakeRunner(nconvert)
    assert compress(runner, png)
    assert runner.calls[0][:5] == ("nconvert", "-out", "png", "-clevel", "6")
    assert png.read_bytes() == b"small"
    assert not (tmp_path / "a.scr01_1.png").exists()


def test_compress_failure_keeps_original(tmp_path: Path) -> None:
    png = tmp_path / "a.scr01.png"
    png.write_bytes(b"big")
    runner = FakeRunner(lambda argv: CommandResult(argv, 1, "", "bad"))
    assert not compress(runner, png)
    assert png.read_bytes() == b"big"
