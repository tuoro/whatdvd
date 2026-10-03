"""做种前检查：找出和上传无关的文件（PTP 2.1.3：不要放样片、截图、Desktop.ini/thumbs.db 等）。

只提示，不删除。和抓盘有关的文件（.nfo、日志、校验文件）以及 DVD 结构本身（AUDIO_TS、JACKET_P）不提示。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# 系统或其他软件生成的文件和目录（小写比较）
_SYSTEM_FILES = {"thumbs.db", "ehthumbs.db", "desktop.ini", ".ds_store", ".directory"}
_SYSTEM_DIRS = {"@eadir", ".appledouble", "__macosx", "$recycle.bin", "system volume information", ".trash"}
_IMAGES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
_VIDEOS = {".mkv", ".mp4", ".avi", ".m2ts", ".ts", ".wmv", ".mov", ".m4v"}
_INCOMPLETE = {".part", ".!qb", ".!ut", ".bc!", ".crdownload", ".tmp"}
_SHORTCUTS = {".url", ".lnk", ".webloc"}
# 混进原盘的零散音视频流（PTP：这样的种子可以被替换，删掉再发）
_STREAMS = {".h264", ".264", ".avc", ".m2v", ".mpv", ".ac3", ".eac3", ".dts", ".mpa", ".mp2", ".wav", ".pcm", ".lpcm", ".sup"}
_RELEASE_SUFFIXES = (".torrent", ".mediainfo.txt", ".post.txt")
_DVD_FILES = {".ifo", ".bup", ".vob"}
# DVD 结构中的目录，里面的文件不检查
_DVD_DIRS = {"audio_ts", "jacket_p"}


@dataclass(frozen=True)
class ExtraFile:
    path: Path
    """相对于检查目录的路径。"""
    reason: str


def _reason(path: Path) -> str | None:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name in _SYSTEM_FILES or name.startswith("._"):
        return "系统生成的文件"
    if name.endswith(_RELEASE_SUFFIXES):
        return "种子或发布用的文件"
    if suffix in _INCOMPLETE:
        return "没下载完的文件"
    if suffix in _IMAGES:
        return "图片（截图或封面）"
    if suffix in _VIDEOS or "sample" in name:
        return "视频文件（样片？）"
    if suffix in _SHORTCUTS:
        return "快捷方式"
    if suffix in _STREAMS:
        return "零散的音视频流（PTP：混进原盘的这类文件要删掉）"
    if path.parent.name.upper() == "VIDEO_TS" and suffix not in _DVD_FILES:
        return "VIDEO_TS 中不属于 DVD 结构的文件"
    return None


def find_extra_files(root: Path) -> list[ExtraFile]:
    """root 为要做种的目录；单个文件（例如 ISO）不检查。"""
    if not root.is_dir():
        return []
    found: list[ExtraFile] = []

    def walk(directory: Path) -> None:
        for entry in sorted(directory.iterdir(), key=lambda p: p.name):
            relative = entry.relative_to(root)
            if entry.is_dir() and not entry.is_symlink():
                if entry.name.lower() in _SYSTEM_DIRS:
                    found.append(ExtraFile(relative, "系统生成的目录"))
                elif entry.name.lower() not in _DVD_DIRS:
                    walk(entry)
            elif (reason := _reason(entry)) is not None:
                found.append(ExtraFile(relative, reason))

    walk(root)
    return found


def describe_extra_files(found: list[ExtraFile], limit: int = 20) -> list[str]:
    if not found:
        return []
    lines = [f"注意：发现 {len(found)} 个和上传无关的文件，建议删除后再做种（PTP 2.1.3）："]
    lines += [f"  {item.path.as_posix()}（{item.reason}）" for item in found[:limit]]
    if len(found) > limit:
        lines.append(f"  ……另有 {len(found) - limit} 个")
    return lines
