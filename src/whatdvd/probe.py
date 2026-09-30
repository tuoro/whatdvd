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
    par_text: str
    """MediaInfo 原样输出的 PAR，例如 "1.422"。"""
    dar: float | None = None
    """MediaInfo 的显示宽高比（小数，例如 1.778），没有时为 None。"""


def probe_video(runner: Runner, vob: Path) -> VideoInfo:
    """读取第一条视频流的 PAR 和编码尺寸。

    jietu 从 `mediainfo -f` 的 "Pixel aspect ratio" 行取 PAR，这里的 %PixelAspectRatio% 是同一个值。
    jietu 的宽高取自 ffmpeg，对 VOB 来说与 MediaInfo 的 Width / Height 相同。
    """
    output = runner.run(["mediainfo", f"--Inform={_VIDEO_INFORM}", vob]).stdout
    match = _VIDEO_PATTERN.search(output)
    if match is None:
        raise ProbeError(f"MediaInfo 没有给出视频流的 PAR 和尺寸：{vob.name}")
    par_text, width, height, dar_text = match.groups()
    return VideoInfo(
        width=int(width),
        height=int(height),
        par=float(par_text),
        par_text=par_text,
        dar=float(dar_text) if dar_text else None,
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
