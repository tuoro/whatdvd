"""ISO 按需解包：用 7z 列出目录，先解出各组的 VTS_xx_0.IFO 读时长选主片，再只解包选中的 VOB。

不 mount、不整盘解包，也不需要 root。
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .dvd import Disc, ScanError, Selection, is_title_ifo, select_title, title_durations
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


def video_ts_files(entries: list[IsoEntry]) -> list[IsoEntry]:
    return [e for e in entries if PurePosixPath(e.path).parent.name.upper() == "VIDEO_TS"]


def entry_name(entry: IsoEntry) -> str:
    return PurePosixPath(entry.path).name


def select_files(entries: list[IsoEntry], durations: dict[str, float]) -> Selection[IsoEntry]:
    """与文件夹相同的规则，只看 VIDEO_TS 中的文件。"""
    files = video_ts_files(entries)
    if not files:
        raise ScanError("ISO 中没有 VIDEO_TS 目录，或目录是空的")
    return select_title(files, entry_name, lambda e: e.size, durations)


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
    entries = parse_listing(runner.run(["7z", "l", "-slt", iso]).stdout)
    title_ifos = [e for e in video_ts_files(entries) if is_title_ifo(entry_name(e))]
    name = iso.stem
    if temp_root is not None:
        temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="whatdvd-", dir=temp_root) as temp_dir:
        # 解析符号链接，保证解出的文件都在 temp 之下，MediaInfo 用相对路径。
        temp = Path(temp_dir).resolve()
        target = temp / name

        def extract(wanted: list[IsoEntry]) -> list[Path]:
            paths = [_extracted_path(target, entry) for entry in wanted]
            todo = [e.path for e, p in zip(wanted, paths, strict=True) if not p.is_file()]
            if todo:
                runner.run(["7z", "x", "-y", "-bso0", "-bsp0", f"-o{target}", iso, *todo])
            missing = [p for p in paths if not p.is_file()]
            if missing:
                raise ScanError(f"7z 没有解出：{'、'.join(p.name for p in missing)}")
            return paths

        # IFO 只有几十 KB，先全部解出读时长
        ifo_paths = extract(title_ifos)
        durations = title_durations(runner, zip(map(entry_name, title_ifos), ifo_paths, strict=True))
        selection = select_files(entries, durations)
        wanted = [selection.vob] if selection.ifo is None else [selection.vob, selection.ifo]
        paths = extract(wanted)
        yield Disc(
            name=name,
            video_ts=paths[0].parent,
            vob=paths[0],
            ifo=paths[1] if selection.ifo is not None else None,
            total_bytes=iso.stat().st_size,
            mediainfo_root=temp,
            title_set=selection.title_set,
            title_duration=selection.duration,
        )
