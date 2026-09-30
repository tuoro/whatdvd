"""把用户输入的路径展开成一张张盘，文件夹和 ISO 统一成 Disc。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .dvd import Disc, ScanError, scan_disc
from .iso import open_iso
from .runner import Runner


def is_iso(path: Path) -> bool:
    return path.suffix.lower() == ".iso"


def find_sources(path: Path) -> list[Path]:
    """输入可以是 ISO 文件、VIDEO_TS、盘目录，或包含多张盘（文件夹或 ISO）的目录。

    返回的每一项是一张盘：VIDEO_TS 目录或 ISO 文件。
    """
    path = path.absolute()
    if path.is_file():
        if is_iso(path):
            return [path]
        raise ScanError(f"不是 ISO 文件：{path}")
    if not path.is_dir():
        raise ScanError(f"路径不存在：{path}")
    if path.name.upper() == "VIDEO_TS":
        return [path]
    found = [
        p
        for p in path.rglob("*")
        if (p.is_dir() and p.name.upper() == "VIDEO_TS") or (p.is_file() and is_iso(p))
    ]
    if not found:
        raise ScanError(f"没有找到 VIDEO_TS 目录或 ISO 文件：{path}")
    return sorted(found, key=str)


@contextmanager
def open_disc(runner: Runner, source: Path, input_path: Path, temp_root: Path | None = None) -> Iterator[Disc]:
    """ISO 解包到临时目录，离开 with 块时清理；文件夹直接读取。

    文件夹的 MediaInfo 删除 "输入路径的上级目录/" 前缀，同 jietu。
    """
    if is_iso(source):
        with open_iso(runner, source, temp_root) as disc:
            yield disc
    else:
        yield scan_disc(source, os.path.join(str(input_path.absolute().parent), ""))
