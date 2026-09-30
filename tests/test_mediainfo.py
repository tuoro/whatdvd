from pathlib import Path

from conftest import FakeRunner, ok
from whatdvd.mediainfo import build_report, relative_arg
from whatdvd.runner import CommandResult


def test_relative_arg() -> None:
    root = Path("/srv/dl")
    assert relative_arg(Path("/srv/dl/Disc/VIDEO_TS/VTS_01_1.VOB"), root) == "Disc/VIDEO_TS/VTS_01_1.VOB"
    assert relative_arg(Path("/srv/dl/-Disc/VIDEO_TS/VTS_01_1.VOB"), root) == "./-Disc/VIDEO_TS/VTS_01_1.VOB"
    assert relative_arg(Path("/other/VTS_01_1.VOB"), root) == "/other/VTS_01_1.VOB"


def _mediainfo(argv: tuple[str, ...]) -> CommandResult:
    return ok(argv, f"General\nComplete name : {argv[-1]}\n\n")


def test_report_runs_in_root_with_relative_paths_and_keeps_output() -> None:
    runner = FakeRunner(_mediainfo)
    root = Path("/srv/dl")
    report = build_report(
        runner,
        Path("/srv/dl/Disc/VIDEO_TS/VTS_01_1.VOB"),
        Path("/srv/dl/Disc/VIDEO_TS/VTS_01_0.IFO"),
        root,
    )
    assert report == (
        "General\nComplete name : Disc/VIDEO_TS/VTS_01_1.VOB\n\n"
        "\n\n\n"
        "General\nComplete name : Disc/VIDEO_TS/VTS_01_0.IFO\n\n"
    )
    assert runner.calls == [
        ("mediainfo", "Disc/VIDEO_TS/VTS_01_1.VOB"),
        ("mediainfo", "Disc/VIDEO_TS/VTS_01_0.IFO"),
    ]
    assert runner.cwds == [root, root]


def test_report_without_ifo() -> None:
    runner = FakeRunner(_mediainfo)
    report = build_report(runner, Path("/a/D/VIDEO_TS/VTS_01_1.VOB"), None, Path("/a"))
    assert report == "General\nComplete name : D/VIDEO_TS/VTS_01_1.VOB\n\n"
    assert len(runner.calls) == 1
