"""单张盘的处理流程：识别 → 截图 → MediaInfo（顺序同 jietu）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .dvd import Disc, tv_standard
from .mediainfo import build_report
from .naming import mediainfo_name, screenshot_name
from .probe import VideoInfo, probe_duration, probe_video
from .resolution import screenshot_size
from .runner import Runner
from .screenshots import capture, compress, format_timestamp, timestamps

Progress = Callable[[str], None]


@dataclass(frozen=True)
class Analysis:
    disc: Disc
    video: VideoInfo
    size: tuple[int, int]
    """截图尺寸。"""
    duration: int
    """选中 VOB 的时长（秒）。"""

    @property
    def standard(self) -> str | None:
        return tv_standard(self.video.height)


@dataclass(frozen=True)
class Shot:
    index: int
    at: int
    path: Path
    ok: bool


@dataclass(frozen=True)
class DiscOutput:
    mediainfo: Path
    shots: list[Shot]

    @property
    def failed(self) -> list[Shot]:
        return [shot for shot in self.shots if not shot.ok]


def analyze(runner: Runner, disc: Disc, aspect: str = "minfo") -> Analysis:
    video = probe_video(runner, disc.vob)
    return Analysis(
        disc=disc,
        video=video,
        size=screenshot_size(video.width, video.height, video.par, video.dar, aspect),
        duration=probe_duration(runner, disc.vob),
    )


def generate(
    runner: Runner,
    analysis: Analysis,
    *,
    count: int,
    output_dir: Path,
    progress: Progress = lambda _: None,
) -> DiscOutput:
    disc = analysis.disc
    output_dir.mkdir(parents=True, exist_ok=True)
    use_nconvert = runner.which("nconvert") is not None

    shots: list[Shot] = []
    for index, at in enumerate(timestamps(analysis.duration, count), start=1):
        path = output_dir / screenshot_name(disc.file_title, index, count)
        ok = capture(runner, disc.vob, at, analysis.size, path)
        note = ""
        if ok and use_nconvert and not compress(runner, path):
            note = "，nconvert 压缩失败，保留原图"
        progress(f"{path.name}（{format_timestamp(at)}）{'完成' if ok else '失败'}{note}")
        shots.append(Shot(index=index, at=at, path=path, ok=ok))

    report_path = output_dir / mediainfo_name(disc.file_title)
    report_path.write_text(build_report(runner, disc.vob, disc.ifo, disc.mediainfo_root), encoding="utf-8")
    progress(f"{report_path.name} 完成")
    return DiscOutput(mediainfo=report_path, shots=shots)
