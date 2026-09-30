from pathlib import Path

import pytest

from conftest import FakeRunner, ok
from whatdvd.probe import ProbeError, probe_duration, probe_video


def test_probe_video_reads_first_stream() -> None:
    runner = FakeRunner(lambda argv: ok(argv, "1.422|720|576|1.778|1.185|720|480|1.778|\n"))
    info = probe_video(runner, Path("x.VOB"))
    assert (info.width, info.height, info.par, info.par_text, info.dar) == (720, 576, 1.422, "1.422", 1.778)
    assert runner.calls[0][1] == "--Inform=Video;%PixelAspectRatio%|%Width%|%Height%|%DisplayAspectRatio%|"


def test_probe_video_without_dar() -> None:
    runner = FakeRunner(lambda argv: ok(argv, "1.422|720|576||\n"))
    assert probe_video(runner, Path("x.VOB")).dar is None


def test_probe_video_without_video_stream() -> None:
    runner = FakeRunner(lambda argv: ok(argv, "\n"))
    with pytest.raises(ProbeError):
        probe_video(runner, Path("x.VOB"))


@pytest.mark.parametrize(("output", "seconds"), [("1523.990000\n", 1523), ("30.016000", 30)])
def test_probe_duration_truncates(output: str, seconds: int) -> None:
    runner = FakeRunner(lambda argv: ok(argv, output))
    assert probe_duration(runner, Path("x.VOB")) == seconds


def test_probe_duration_not_available() -> None:
    runner = FakeRunner(lambda argv: ok(argv, "N/A\n"))
    with pytest.raises(ProbeError, match="时长"):
        probe_duration(runner, Path("x.VOB"))
