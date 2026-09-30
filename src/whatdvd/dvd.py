"""定位 VIDEO_TS，按 jietu 的规则选出截图用的 VOB 和 MediaInfo 用的 IFO。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .naming import clean_title

# 单层 DVD 的容量上限（字节），超过即为 DVD9。
DVD5_MAX_BYTES = 4_700_372_992


class ScanError(RuntimeError):
    pass


@dataclass(frozen=True)
class Disc:
    video_ts: Path
    vob: Path
    ifo: Path | None
    total_bytes: int

    @property
    def root(self) -> Path:
        """盘目录，即 VIDEO_TS 的上一级（jietu 的 disk_path）。"""
        return self.video_ts.parent

    @property
    def file_title(self) -> str:
        """输出文件名前缀：<盘名>.<VOB 文件名>，都经过清理。"""
        return f"{clean_title(self.root.name)}.{clean_title(self.vob.name)}"

    @property
    def media_type(self) -> str:
        return "DVD5" if self.total_bytes <= DVD5_MAX_BYTES else "DVD9"


def find_video_ts_dirs(path: Path) -> list[Path]:
    """输入可以是 VIDEO_TS 本身、盘目录，或包含多张盘的目录。"""
    path = path.absolute()
    if not path.is_dir():
        raise ScanError(f"不是目录：{path}")
    if path.name.upper() == "VIDEO_TS":
        return [path]
    found = [p for p in path.rglob("*") if p.is_dir() and p.name.upper() == "VIDEO_TS"]
    if not found:
        raise ScanError(f"没有找到 VIDEO_TS 目录：{path}")
    return sorted(found, key=str)


def largest_file(files: Iterable[Path]) -> Path | None:
    """同 jietu 的 `ls -S | head -1`：取体积最大的文件，大小相同时按路径升序取第一个。"""
    sized = [(-f.stat().st_size, str(f), f) for f in files]
    return min(sized)[2] if sized else None


def scan_disc(video_ts: Path) -> Disc:
    files = [p for p in video_ts.iterdir() if p.is_file()]
    vob = largest_file(files)
    if vob is None:
        raise ScanError(f"VIDEO_TS 是空的：{video_ts}")
    if vob.suffix.upper() != ".VOB":
        raise ScanError(f"VIDEO_TS 中最大的文件不是 VOB：{vob.name}")
    ifo = largest_file(p for p in files if p.suffix.upper() == ".IFO")
    total = sum(p.stat().st_size for p in files)
    return Disc(video_ts=video_ts, vob=vob, ifo=ifo, total_bytes=total)


def tv_standard(height: int) -> str | None:
    return {576: "PAL", 480: "NTSC"}.get(height)
