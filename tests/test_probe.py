from pathlib import Path

import pytest

from conftest import FakeRunner, ok
from whatdvd.probe import ProbeError, ffprobe_dar, ifo_aspect, probe_duration, probe_video
from whatdvd.runner import CommandResult


def make_ifo(path: Path, aspect_bits: int, magic: bytes = b"DVDVIDEO-VTS") -> Path:
    data = bytearray(0x400)
    data[: len(magic)] = magic
    data[0x200] = 0x40 | (1 << 4) | (aspect_bits << 2)  # MPEG-2、PAL
    path.write_bytes(bytes(data))
    return path


def _runner(mediainfo: str, ffprobe: str = "") -> FakeRunner:
    def handler(argv: tuple[str, ...]) -> CommandResult:
        return ok(argv, mediainfo if argv[0] == "mediainfo" else ffprobe)

    return FakeRunner(handler)


@pytest.mark.parametrize(("bits", "expected"), [(0, 4 / 3), (3, 16 / 9), (1, None), (2, None)])
def test_ifo_aspect(tmp_path: Path, bits: int, expected: float | None) -> None:
    assert ifo_aspect(make_ifo(tmp_path / "VTS_01_0.IFO", bits)) == expected


def test_ifo_aspect_rejects_other_files(tmp_path: Path) -> None:
    assert ifo_aspect(make_ifo(tmp_path / "VIDEO_TS.IFO", 3, magic=b"DVDVIDEO-VMG")) is None
    assert ifo_aspect(tmp_path / "missing.IFO") is None
    short = tmp_path / "short.IFO"
    short.write_bytes(b"DVDVIDEO-VTS")
    assert ifo_aspect(short) is None


@pytest.mark.parametrize(("output", "expected"), [("16:9\n", 16 / 9), ("4:3", 4 / 3), ("0:1", None), ("N/A", None), ("", None)])
def test_ffprobe_dar(output: str, expected: float | None) -> None:
    assert ffprobe_dar(FakeRunner(lambda argv: ok(argv, output)), Path("x.VOB")) == expected


def test_probe_video_prefers_ifo_over_mediainfo(tmp_path: Path) -> None:
    # PAL 16:9 的显示区域标为 540 宽：MediaInfo 报 PAR 1.896、DAR 2.370
    runner = _runner("1.896|720|576|2.370|\n", "16:9")
    info = probe_video(runner, Path("x.VOB"), make_ifo(tmp_path / "VTS_01_0.IFO", 3))
    assert (info.width, info.height, info.par_text, info.dar_source) == (720, 576, "1.422", "IFO")
    assert info.dar == 16 / 9
    assert (info.mediainfo_par, info.mediainfo_dar) == (1.896, 2.37)
    assert info.mediainfo_disagrees
    assert [call[0] for call in runner.calls] == ["mediainfo"]  # 有 IFO 就不调用 ffprobe
    assert runner.calls[0][1] == "--Inform=Video;%PixelAspectRatio%|%Width%|%Height%|%DisplayAspectRatio%|"


def test_probe_video_falls_back_to_ffprobe() -> None:
    info = probe_video(_runner("1.896|720|576|2.370|\n", "16:9"), Path("x.VOB"))
    assert (info.par_text, info.dar_source) == ("1.422", "ffprobe")
    assert info.mediainfo_disagrees


def test_probe_video_falls_back_to_mediainfo() -> None:
    info = probe_video(_runner("1.422|720|576|1.778|1.185|720|480|1.778|\n"), Path("x.VOB"))
    assert (info.width, info.height, info.dar, info.dar_source) == (720, 576, 1.778, "MediaInfo")
    assert info.par_text == "1.422"
    assert not info.mediainfo_disagrees


def test_probe_video_agreeing_sources_do_not_warn(tmp_path: Path) -> None:
    info = probe_video(_runner("1.422|720|576|1.778|\n"), Path("x.VOB"), make_ifo(tmp_path / "VTS_01_0.IFO", 3))
    assert info.dar_source == "IFO" and not info.mediainfo_disagrees


def test_probe_video_without_any_dar() -> None:
    info = probe_video(_runner("1.422|720|576||\n"), Path("x.VOB"))
    assert info.dar is None and info.par == 1.422


def test_probe_video_without_video_stream() -> None:
    with pytest.raises(ProbeError):
        probe_video(_runner("\n"), Path("x.VOB"))


@pytest.mark.parametrize(("output", "seconds"), [("1523.990000\n", 1523), ("30.016000", 30)])
def test_probe_duration_truncates(output: str, seconds: int) -> None:
    runner = FakeRunner(lambda argv: ok(argv, output))
    assert probe_duration(runner, Path("x.VOB")) == seconds


def test_probe_duration_not_available() -> None:
    runner = FakeRunner(lambda argv: ok(argv, "N/A\n"))
    with pytest.raises(ProbeError, match="时长"):
        probe_duration(runner, Path("x.VOB"))
