from pathlib import Path

import pytest

from conftest import FakeRunner, ok
from whatdvd.runner import CommandResult
from whatdvd.screenshots import capture, compress, format_timestamp, timestamps, usable_range


@pytest.mark.parametrize(
    ("duration", "count", "expected"),
    [
        # 5% 起，间隔 (90% − 5%) ÷ 张数，同 Upload-Assistant
        (1500, 11, [75 + 115 * i for i in range(11)]),  # 25 分钟的 VOB：1:15 到 20:25
        (1800, 3, [90, 600, 1110]),
        (70, 5, [3, 15, 27, 39, 51]),
        (1800, 1, [90]),
        (0, 2, [0, 0]),
    ],
)
def test_timestamps_spread_over_5_to_90_percent(duration: int, count: int, expected: list[int]) -> None:
    assert timestamps(duration, count) == expected
    assert all(at <= usable_range(duration)[1] for at in timestamps(duration, count))


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
