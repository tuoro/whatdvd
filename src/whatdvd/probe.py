"""从 VOB 读取截图需要的视频参数和时长，从 IFO 读取标题时长。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .runner import Runner

# 结尾的 "|" 用来隔开多条视频流的输出。
_VIDEO_INFORM = "Video;%PixelAspectRatio%|%Width%|%Height%|%DisplayAspectRatio%|"
_VIDEO_PATTERN = re.compile(r"([0-9.]+)\|(\d+)\|(\d+)\|([0-9.]*)\|")


class ProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    par: float
    """像素宽高比：有 DAR 时按 DAR × 高 ÷ 宽 算出，否则为 MediaInfo 的 PAR。"""
    par_text: str
    """PAR 的三位小数写法，例如 "1.422"。"""
    dar: float | None = None
    """显示宽高比（例如 1.778），没有时为 None。"""
    dar_source: str = "MediaInfo"
    """DAR 的来源：IFO、ffprobe 或 MediaInfo。"""
    mediainfo_par: float | None = None
    mediainfo_dar: float | None = None
    """MediaInfo 从 VOB 读到的原始值，用于提示与 IFO 不一致的情况。"""

    @property
    def mediainfo_disagrees(self) -> bool:
        """MediaInfo 的 DAR 与实际采用的相差超过 0.02（例如 PAL 16:9 的 pan & scan 显示区域）。"""
        return (
            self.dar_source != "MediaInfo"
            and self.dar is not None
            and (self.mediainfo_dar is None or abs(self.mediainfo_dar - self.dar) > 0.02)
        )


# VTS_xx_0.IFO 的视频属性（VTSI_MAT 偏移 0x200），第 3–2 位是显示比例：0 = 4:3，3 = 16:9。
_IFO_MAGIC = b"DVDVIDEO-VTS"
_IFO_VIDEO_ATTR = 0x200
_IFO_ASPECTS = {0: 4 / 3, 3: 16 / 9}


def ifo_aspect(ifo: Path) -> float | None:
    """读取标题组 IFO 中的显示比例标记，这是播放器实际使用的比例。读不到时返回 None。"""
    try:
        with ifo.open("rb") as f:
            data = f.read(_IFO_VIDEO_ATTR + 1)
    except OSError:
        return None
    if len(data) <= _IFO_VIDEO_ATTR or not data.startswith(_IFO_MAGIC):
        return None
    return _IFO_ASPECTS.get((data[_IFO_VIDEO_ATTR] >> 2) & 3)


def ffprobe_dar(runner: Runner, vob: Path) -> float | None:
    """ffprobe 的显示比例，例如 "16:9"。ffmpeg 会忽略不合理的 MPEG-2 显示区域。"""
    output = runner.run(
        [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=display_aspect_ratio",
            "-of", "default=noprint_wrappers=1:nokey=1",
            vob,
        ],
        check=False,
    ).stdout.strip()
    num, _, den = output.partition(":")
    try:
        value = int(num) / int(den)
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def probe_video(runner: Runner, vob: Path, ifo: Path | None = None) -> VideoInfo:
    """读取第一条视频流的编码尺寸和显示比例。

    宽高取自 MediaInfo。显示比例依次取：标题组 IFO 的比例标记 → ffprobe → MediaInfo。
    MediaInfo 会按 MPEG-2 的显示区域（sequence display extension）计算比例，
    PAL 16:9 盘常把显示宽度标为 540（供 4:3 电视 pan & scan），MediaInfo 因此报 PAR 1.896、DAR 2.370，
    截图会被拉成 1366x576。IFO 的标记和 ffprobe 都不受影响。
    """
    output = runner.run(["mediainfo", f"--Inform={_VIDEO_INFORM}", vob]).stdout
    match = _VIDEO_PATTERN.search(output)
    if match is None:
        raise ProbeError(f"MediaInfo 没有给出视频流的 PAR 和尺寸：{vob.name}")
    par_text, width_text, height_text, dar_text = match.groups()
    width, height = int(width_text), int(height_text)
    mediainfo_par = float(par_text)
    mediainfo_dar = float(dar_text) if dar_text else None

    dar: float | None = None
    source = "MediaInfo"
    if ifo is not None and (dar := ifo_aspect(ifo)) is not None:
        source = "IFO"
    elif (dar := ffprobe_dar(runner, vob)) is not None:
        source = "ffprobe"
    else:
        dar = mediainfo_dar

    par = dar * height / width if dar is not None else mediainfo_par
    return VideoInfo(
        width=width,
        height=height,
        par=par,
        par_text=f"{par:.3f}",
        dar=dar,
        dar_source=source,
        mediainfo_par=mediainfo_par,
        mediainfo_dar=mediainfo_dar,
    )


def probe_duration(runner: Runner, vob: Path) -> int:
    """VOB 的时长，截断到整秒（jietu 取 ffmpeg Duration 的 HH:MM:SS 部分）。"""
    output = runner.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            vob,
        ]
    ).stdout.strip()
    try:
        return int(float(output))
    except ValueError:
        raise ProbeError(f"ffprobe 没有给出有效时长：{vob.name}（输出：{output!r}）") from None


def ifo_duration(runner: Runner, ifo: Path) -> float | None:
    """IFO 的标题时长（秒），同 Upload-Assistant：取 MediaInfo JSON 中 General 之后第一条轨道的 Duration。

    读不出时返回 None。
    """
    result = runner.run(["mediainfo", "--Output=JSON", ifo], check=False)
    try:
        tracks = json.loads(result.stdout)["media"]["track"]
        return float(tracks[1]["Duration"])
    except (ValueError, KeyError, IndexError, TypeError):
        return None
