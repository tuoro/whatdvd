"""DVD 盘的数据结构，以及选出截图用的 VOB 和 MediaInfo 用的 IFO。

主片按 Upload-Assistant 的做法，以各组 VTS_xx_0.IFO 的时长选出；组内仍按 jietu 取最大的 VOB。
IFO 都读不出时长时，退回 jietu 的规则：盘内最大的文件和最大的 IFO。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from .naming import clean_title
from .probe import ifo_duration
from .runner import Runner

# 单层 DVD 的容量上限（字节），超过即为 DVD9。
DVD5_MAX_BYTES = 4_700_372_992

T = TypeVar("T")


class ScanError(RuntimeError):
    pass


@dataclass(frozen=True)
class Disc:
    name: str
    """盘名：VIDEO_TS 的上一级目录名（jietu 的 disk_title），或 ISO 文件名去掉扩展名。"""
    video_ts: Path
    vob: Path
    ifo: Path | None
    total_bytes: int
    """文件夹为 VIDEO_TS 内文件总和，ISO 为 ISO 文件大小。"""
    mediainfo_root: Path
    """运行 mediainfo 的目录（jietu 的 "${FileLoc}"），MediaInfo 中的路径相对于它。"""
    title_set: str | None = None
    """主片所在的组号（例如 "02"）；None 表示 IFO 中没有时长，按最大文件选取。"""
    title_duration: float | None = None
    """主片组 IFO 中的时长（秒）。"""
    skipped_sets: tuple[SkippedSet, ...] = ()
    """按码率判为假标题而跳过的组。"""

    @property
    def file_title(self) -> str:
        """输出文件名前缀：<盘名>.<VOB 文件名>，都经过清理。"""
        return f"{clean_title(self.name)}.{clean_title(self.vob.name)}"

    @property
    def title_ifo(self) -> Path | None:
        """与 VOB 同一组的 VTS_xx_0.IFO（例如 VIDEO_TS.IFO 或别组的 IFO 不算）。"""
        vob = vts_part(self.vob.name)
        if self.ifo is None or vob is None or vts_part(self.ifo.name) != (vob[0], 0, "IFO"):
            return None
        return self.ifo

    @property
    def media_type(self) -> str:
        return "DVD5" if self.total_bytes <= DVD5_MAX_BYTES else "DVD9"


def pick_largest(items: Iterable[T], size: Callable[[T], int], name: Callable[[T], str]) -> T | None:
    """同 jietu 的 `ls -S | head -1`：取体积最大的，大小相同时按名称升序取第一个。"""
    best: T | None = None
    best_key: tuple[int, str] | None = None
    for item in items:
        key = (-size(item), name(item))
        if best_key is None or key < best_key:
            best, best_key = item, key
    return best


def largest_file(files: Iterable[Path]) -> Path | None:
    return pick_largest(files, lambda f: f.stat().st_size, str)


def is_vob(name: str) -> bool:
    return name.upper().endswith(".VOB")


def is_ifo(name: str) -> bool:
    return name.upper().endswith(".IFO")


_VTS = re.compile(r"^VTS_(\d{2})_(\d)\.(VOB|IFO)$", re.IGNORECASE)

# 平均码率低于此值（bit/s）的组判为假标题：复制保护盘常在 IFO 中写入很长的时长，但 VOB 很小。
# 正常的 DVD 视频至少有 2–3 Mbps。这条规则是本项目加的，Upload-Assistant 没有。
MIN_TITLE_BITRATE = 500_000

# 后面的组比当前主片长 10% 以上才替换，同 Upload-Assistant：剧集盘会选中第一集。
MAIN_SET_MARGIN = 1.10


def vts_part(name: str) -> tuple[str, int, str] | None:
    """"VTS_02_1.VOB" → ("02", 1, "VOB")。"""
    match = _VTS.match(name)
    return (match[1], int(match[2]), match[3].upper()) if match else None


def is_title_ifo(name: str) -> bool:
    part = vts_part(name)
    return part is not None and part[1:] == (0, "IFO")


def pick_main_set(durations: Mapping[str, float]) -> str | None:
    """按组号顺序比较时长，同 Upload-Assistant。时长无效（<= 0）的组跳过。"""
    main: str | None = None
    main_duration = 0.0
    for title_set in sorted(durations):
        duration = durations[title_set]
        if duration > 0 and (main is None or duration > main_duration * MAIN_SET_MARGIN):
            main, main_duration = title_set, duration
    return main


@dataclass(frozen=True)
class SkippedSet:
    title_set: str
    duration: float
    vob_bytes: int
    """该组标题 VOB（不含 _0）的总大小。"""

    @property
    def bitrate(self) -> float:
        return self.vob_bytes * 8 / self.duration


@dataclass(frozen=True)
class Selection(Generic[T]):
    vob: T
    ifo: T | None
    title_set: str | None
    duration: float | None
    skipped: tuple[SkippedSet, ...] = ()


def title_vob_bytes(files: Sequence[T], name: Callable[[T], str], size: Callable[[T], int]) -> dict[str, int]:
    """组号 → 标题 VOB（不含 _0 菜单）的总大小。"""
    totals: dict[str, int] = {}
    for f in files:
        part = vts_part(name(f))
        if part is not None and part[2] == "VOB" and part[1] >= 1:
            totals[part[0]] = totals.get(part[0], 0) + size(f)
    return totals


def filter_fake_sets(
    durations: Mapping[str, float], vob_bytes: Mapping[str, int]
) -> tuple[dict[str, float], tuple[SkippedSet, ...]]:
    """去掉平均码率低于 MIN_TITLE_BITRATE 的组（IFO 时长和 VOB 大小对不上）。"""
    kept: dict[str, float] = {}
    skipped: list[SkippedSet] = []
    for title_set, duration in durations.items():
        if duration <= 0:
            continue
        info = SkippedSet(title_set, duration, vob_bytes.get(title_set, 0))
        if info.bitrate < MIN_TITLE_BITRATE:
            skipped.append(info)
        else:
            kept[title_set] = duration
    return kept, tuple(sorted(skipped, key=lambda s: s.title_set))


def select_title(
    files: Sequence[T],
    name: Callable[[T], str],
    size: Callable[[T], int],
    durations: Mapping[str, float],
) -> Selection[T]:
    """name 返回文件名（不含目录）。durations 为 组号 → IFO 时长（秒）。"""
    durations, skipped = filter_fake_sets(durations, title_vob_bytes(files, name, size))
    main = pick_main_set(durations)
    if main is not None:
        parts = [f for f in files if (p := vts_part(name(f))) and p[0] == main and p[2] == "VOB" and p[1] >= 1]
        vob = pick_largest(parts, size, name)
        if vob is not None:
            ifo = next((f for f in files if vts_part(name(f)) == (main, 0, "IFO")), None)
            return Selection(vob=vob, ifo=ifo, title_set=main, duration=durations[main], skipped=skipped)

    vob = pick_largest(files, size, name)
    if vob is None:
        raise ScanError("VIDEO_TS 中没有文件")
    if not is_vob(name(vob)):
        raise ScanError(f"VIDEO_TS 中最大的文件不是 VOB：{name(vob)}")
    ifo = pick_largest((f for f in files if is_ifo(name(f))), size, name)
    return Selection(vob=vob, ifo=ifo, title_set=None, duration=None, skipped=skipped)


def title_durations(runner: Runner, ifos: Iterable[tuple[str, Path]]) -> dict[str, float]:
    """(IFO 文件名, 路径) → {组号: 时长}，读不出时长的组不列出。"""
    durations: dict[str, float] = {}
    for name, path in ifos:
        part = vts_part(name)
        if part is None or part[1:] != (0, "IFO"):
            continue
        duration = ifo_duration(runner, path)
        if duration is not None:
            durations[part[0]] = duration
    return durations


def scan_disc(runner: Runner, video_ts: Path, mediainfo_root: Path) -> Disc:
    files = [p for p in video_ts.iterdir() if p.is_file()]
    if not files:
        raise ScanError(f"VIDEO_TS 是空的：{video_ts}")
    durations = title_durations(runner, ((p.name, p) for p in files if is_title_ifo(p.name)))
    try:
        selection = select_title(files, lambda p: p.name, lambda p: p.stat().st_size, durations)
    except ScanError as error:
        raise ScanError(f"{error}（{video_ts}）") from None
    return Disc(
        name=video_ts.parent.name,
        video_ts=video_ts,
        vob=selection.vob,
        ifo=selection.ifo,
        total_bytes=sum(p.stat().st_size for p in files),
        mediainfo_root=mediainfo_root,
        title_set=selection.title_set,
        title_duration=selection.duration,
        skipped_sets=selection.skipped,
    )


def tv_standard(height: int) -> str | None:
    return {576: "PAL", 480: "NTSC"}.get(height)
