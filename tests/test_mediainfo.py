from pathlib import Path

from conftest import FakeRunner, ok
from whatdvd.mediainfo import build_report, strip_path_prefix
from whatdvd.runner import CommandResult


def test_strip_only_first_occurrence_per_line() -> None:
    text = "Complete name : /srv/dl/Disc/VIDEO_TS/VTS_01_1.VOB\nOther : /srv/dl/x /srv/dl/y"
    assert strip_path_prefix(text, "/srv/dl/") == (
        "Complete name : Disc/VIDEO_TS/VTS_01_1.VOB\nOther : x /srv/dl/y"
    )


def _mediainfo(argv: tuple[str, ...]) -> CommandResult:
    return ok(argv, f"General\nComplete name : {argv[-1]}\n\n")


def test_report_puts_vob_first_then_ifo() -> None:
    runner = FakeRunner(_mediainfo)
    report = build_report(
        runner,
        Path("/srv/dl/Disc/VIDEO_TS/VTS_01_1.VOB"),
        Path("/srv/dl/Disc/VIDEO_TS/VTS_01_0.IFO"),
        "/srv/dl/",
    )
    assert report == (
        "General\nComplete name : Disc/VIDEO_TS/VTS_01_1.VOB\n\n"
        "\n\n\n"
        "General\nComplete name : Disc/VIDEO_TS/VTS_01_0.IFO\n\n"
    )
    assert [call[0] for call in runner.calls] == ["mediainfo", "mediainfo"]


def test_report_without_ifo() -> None:
    runner = FakeRunner(_mediainfo)
    report = build_report(runner, Path("/a/D/VIDEO_TS/VTS_01_1.VOB"), None, "/a/")
    assert report == "General\nComplete name : D/VIDEO_TS/VTS_01_1.VOB\n\n"
    assert len(runner.calls) == 1
