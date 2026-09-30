"""DVD 盘的数据结构，以及按 jietu 的规则选出截图用的 VOB 和 MediaInfo 用的 IFO。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from .naming import clean_title

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

    @property
    def file_title(self) -> str:
        """输出文件名前缀：<盘名>.<VOB 文件名>，都经过清理。"""
        return f"{clean_title(self.name)}.{clean_title(self.vob.name)}"

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


def scan_disc(video_ts: Path, mediainfo_root: Path) -> Disc:
    files = [p for p in video_ts.iterdir() if p.is_file()]
    vob = largest_file(files)
    if vob is None:
        raise ScanError(f"VIDEO_TS 是空的：{video_ts}")
    if not is_vob(vob.name):
        raise ScanError(f"VIDEO_TS 中最大的文件不是 VOB：{vob.name}")
    return Disc(
        name=video_ts.parent.name,
        video_ts=video_ts,
        vob=vob,
        ifo=largest_file(p for p in files if is_ifo(p.name)),
        total_bytes=sum(p.stat().st_size for p in files),
        mediainfo_root=mediainfo_root,
    )


def tv_standard(height: int) -> str | None:
    return {576: "PAL", 480: "NTSC"}.get(height)
