"""单张盘的处理流程：识别 → 截图 → MediaInfo（顺序同 jietu）。"""

from __future__ import annotations

import random
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from .dvd import Disc, tv_standard
from .mediainfo import build_report
from .naming import mediainfo_name, screenshot_name
from .probe import VideoInfo, probe_duration, probe_video
from .resolution import screenshot_size
from .runner import Runner
from .screenshots import capture, compress, format_timestamp, timestamps

Progress = Callable[[str, bool], None]
"""日志回调：第二个参数为 True 时算作完成一步（用于进度条）。"""

# 剔除黑屏，同 Upload-Assistant：多截一张，删掉体积最小的；
# 不超过 SMALL_BYTES 的视为黑屏或过渡画面，在随机时间点重截，最多 RETAKE_ATTEMPTS 次，超过 RETAKE_OK_BYTES 即可。
SMALL_BYTES = 120_000
RETAKE_OK_BYTES = 75_000
RETAKE_ATTEMPTS = 3


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


@dataclass(frozen=True)
class _Take:
    at: int
    path: Path | None
    """截图成功时为临时文件，失败为 None。"""

    @property
    def size(self) -> int:
        return self.path.stat().st_size if self.path else 0


def _kib(size: int) -> str:
    return f"{size / 1024:.0f} KiB"


def analyze(runner: Runner, disc: Disc, aspect: str = "ua") -> Analysis:
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
    progress: Progress = lambda message, tick: None,
    dark_filter: bool = True,
    rng: random.Random | None = None,
) -> DiscOutput:
    """取点同 jietu；dark_filter 时多截一张并剔除黑屏（同 Upload-Assistant）。"""
    disc = analysis.disc
    output_dir.mkdir(parents=True, exist_ok=True)
    use_nconvert = runner.which("nconvert") is not None
    total = count + 1 if dark_filter else count

    shots: list[Shot] = []
    with tempfile.TemporaryDirectory(prefix=".whatdvd-", dir=output_dir) as work_dir:
        work = Path(work_dir)
        takes: list[_Take] = []
        for index, at in enumerate(timestamps(analysis.duration, total), start=1):
            path = work / f"{index}.png"
            ok = capture(runner, disc.vob, at, analysis.size, path)
            detail = f"完成，{_kib(path.stat().st_size)}" if ok else "失败"
            progress(f"截图 {index}/{total}（{format_timestamp(at)}）{detail}", True)
            takes.append(_Take(at, path if ok else None))

        if dark_filter:
            takes = _drop_one(takes, progress)
            takes = [_retake_if_small(runner, analysis, take, work, progress, rng or random.Random()) for take in takes]
        takes.sort(key=lambda take: take.at)

        for index, take in enumerate(takes, start=1):
            path = output_dir / screenshot_name(disc.file_title, index, count)
            path.unlink(missing_ok=True)
            if take.path is not None:
                take.path.replace(path)
                if use_nconvert and not compress(runner, path):
                    progress(f"{path.name} nconvert 压缩失败，保留原图", False)
            shots.append(Shot(index=index, at=take.at, path=path, ok=take.path is not None))
    saved = [shot for shot in shots if shot.ok]
    if saved:
        progress(f"保存为 {saved[0].path.name} 等 {len(saved)} 张", False)

    report_path = output_dir / mediainfo_name(disc.file_title)
    report_path.write_text(build_report(runner, disc.vob, disc.ifo, disc.mediainfo_root), encoding="utf-8")
    progress(f"{report_path.name} 完成", True)
    return DiscOutput(mediainfo=report_path, shots=shots)


def _drop_one(takes: list[_Take], progress: Progress) -> list[_Take]:
    """多截的一张：都成功时删掉体积最小的，否则去掉一张失败的。"""
    failed = [take for take in takes if take.path is None]
    if failed:
        drop = failed[-1]
    else:
        drop = min(takes, key=lambda take: take.size)
        progress(f"剔除体积最小的一张（{format_timestamp(drop.at)}，{_kib(drop.size)}）", False)
        assert drop.path is not None
        drop.path.unlink()
    return [take for take in takes if take is not drop]


def _retake_if_small(
    runner: Runner, analysis: Analysis, take: _Take, work: Path, progress: Progress, rng: random.Random
) -> _Take:
    if take.path is None or take.size > SMALL_BYTES:
        return take
    progress(f"{format_timestamp(take.at)} 的截图只有 {_kib(take.size)}，可能是黑屏，换时间点重截", False)
    for attempt in range(1, RETAKE_ATTEMPTS + 1):
        at = rng.randint(0, max(analysis.duration - 2, 0))
        path = work / f"retake-{take.at}-{attempt}.png"
        if not capture(runner, analysis.disc.vob, at, analysis.size, path):
            progress(f"  重截 {attempt}/{RETAKE_ATTEMPTS}（{format_timestamp(at)}）失败", False)
            continue
        size = path.stat().st_size
        if size > RETAKE_OK_BYTES:
            progress(f"  重截 {attempt}/{RETAKE_ATTEMPTS}（{format_timestamp(at)}）完成，{_kib(size)}", False)
            take.path.unlink()
            return replace(take, at=at, path=path)
        progress(f"  重截 {attempt}/{RETAKE_ATTEMPTS}（{format_timestamp(at)}）仍然偏小，{_kib(size)}", False)
        path.unlink()
    progress("  重截都不理想，保留原图", False)
    return take
