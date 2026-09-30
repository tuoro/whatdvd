"""ISO 按需解包：用 7z 列出目录，按 jietu 的规则选文件，只解包选中的 VOB 和 IFO。

不 mount、不整盘解包，也不需要 root。
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .dvd import Disc, ScanError, is_ifo, is_vob, pick_largest
from .runner import Runner


@dataclass(frozen=True)
class IsoEntry:
    path: str
    """ISO 内的路径，以 "/" 分隔，例如 "VIDEO_TS/VTS_01_1.VOB"。"""
    size: int


def parse_listing(text: str) -> list[IsoEntry]:
    """解析 `7z l -slt` 的输出，只保留文件，不含目录。"""
    lines = text.splitlines()
    try:
        start = lines.index("----------") + 1
    except ValueError:
        raise ScanError("无法解析 7z 的列表输出") from None

    entries: list[IsoEntry] = []
    fields: dict[str, str] = {}
    for line in [*lines[start:], ""]:
        if line.strip():
            key, _, value = line.partition("=")
            fields[key.strip()] = value.removeprefix(" ")
            continue
        if "Path" in fields and fields.get("Folder") != "+":
            entries.append(IsoEntry(path=fields["Path"], size=int(fields.get("Size") or 0)))
        fields = {}
    return entries


def select_files(entries: list[IsoEntry]) -> tuple[IsoEntry, IsoEntry | None]:
    """与文件夹相同的规则：VIDEO_TS 中最大的文件作 VOB，最大的 IFO 作 IFO。"""
    files = [e for e in entries if PurePosixPath(e.path).parent.name.upper() == "VIDEO_TS"]
    vob = pick_largest(files, lambda e: e.size, lambda e: e.path)
    if vob is None:
        raise ScanError("ISO 中没有 VIDEO_TS 目录，或目录是空的")
    if not is_vob(vob.path):
        raise ScanError(f"VIDEO_TS 中最大的文件不是 VOB：{vob.path}")
    ifo = pick_largest((e for e in files if is_ifo(e.path)), lambda e: e.size, lambda e: e.path)
    return vob, ifo


def _extracted_path(target: Path, entry: IsoEntry) -> Path:
    path = (target / entry.path).resolve()
    if entry.path.startswith("-") or not path.is_relative_to(target.resolve()):
        raise ScanError(f"ISO 中的路径不安全：{entry.path}")
    return path


@contextmanager
def open_iso(runner: Runner, iso: Path, temp_root: Path | None = None) -> Iterator[Disc]:
    """解包到临时目录 <临时目录>/<ISO 名>/VIDEO_TS/，离开 with 块时删除。

    MediaInfo 中的路径因此显示为 <ISO 名>/VIDEO_TS/…。
    """
    vob, ifo = select_files(parse_listing(runner.run(["7z", "l", "-slt", iso]).stdout))
    name = iso.stem
    if temp_root is not None:
        temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="whatdvd-", dir=temp_root) as temp_dir:
        # 解析符号链接，保证解出的文件都在 temp 之下，MediaInfo 用相对路径。
        temp = Path(temp_dir).resolve()
        target = temp / name
        wanted = [vob] if ifo is None else [vob, ifo]
        paths = [_extracted_path(target, entry) for entry in wanted]
        runner.run(["7z", "x", "-y", "-bso0", "-bsp0", f"-o{target}", iso, *(e.path for e in wanted)])
        missing = [p for p in paths if not p.is_file()]
        if missing:
            raise ScanError(f"7z 没有解出：{'、'.join(p.name for p in missing)}")
        yield Disc(
            name=name,
            video_ts=paths[0].parent,
            vob=paths[0],
            ifo=paths[1] if ifo is not None else None,
            total_bytes=iso.stat().st_size,
            mediainfo_root=temp,
        )
