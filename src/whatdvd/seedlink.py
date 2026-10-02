"""发种目录：用硬链接把下载好的盘放到发种目录，可以另起名字，原始下载不受影响。

硬链接和原文件是同一份数据，不多占空间；原文件删除后硬链接仍然有效。只能建在同一个文件系统上，
不在同一个文件系统时报错（不退回到复制或符号链接）。DVD 结构保持不变，只有最外层的名字可以改。
"""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
from pathlib import Path


class LinkError(RuntimeError):
    pass


def check_name(name: str) -> str:
    """发种名称：最外层文件夹（或 ISO 文件）的名字，不能包含路径。"""
    name = name.strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\0" in name:
        raise LinkError(f"发种名称不能为空，也不能包含 / 或 \\：{name!r}")
    if name.startswith("."):
        raise LinkError(f"发种名称不能以 . 开头：{name}")
    if len(name.encode()) > 255:
        raise LinkError("发种名称太长（超过 255 字节）")
    return name


def target_name(source: Path, name: str | None) -> str:
    """ISO 没写扩展名时补上原来的扩展名。"""
    if not name:
        return source.name
    name = check_name(name)
    if source.is_file() and source.suffix and not name.lower().endswith(source.suffix.lower()):
        name += source.suffix
    return name


def mount_point(path: Path) -> Path:
    path = path.resolve()
    while not os.path.ismount(path):
        path = path.parent
    return path


def same_filesystem(a: Path, b: Path) -> bool:
    """能否在 a、b 之间建硬链接：同一个文件系统，并且在同一个挂载点下。
    Docker 中同一块盘分两次挂载（例如 /media 和 /seed）也不能跨挂载点建硬链接。"""
    return a.stat().st_dev == b.stat().st_dev and mount_point(a) == mount_point(b)


def _files(root: Path) -> list[Path]:
    """root 下所有文件的相对路径（不跟随符号链接目录）。"""
    if root.is_file():
        return [Path()]
    found = []
    for directory, dirs, files in os.walk(root):
        base = Path(directory).relative_to(root)
        found += [base / f for f in files]
        dirs.sort()
    return sorted(found)


def _same_tree(source: Path, target: Path) -> bool:
    """target 是否就是 source 的硬链接副本（文件一一对应且是同一份数据）。"""
    if source.is_file() != target.is_file():
        return False
    files = _files(source)
    if files != _files(target):
        return False
    return all(os.path.samefile(source / f, target / f) for f in files)


def link_tree(source: Path, seed_dir: Path, name: str | None = None) -> tuple[Path, bool]:
    """在 seed_dir 下建 source 的硬链接副本，返回（路径，是否新建）。

    已有同名的副本且是同一份数据时直接使用；同名但内容不同时报错，不覆盖。
    先在临时目录中建好再改名，中途失败不会留下不完整的目录。
    """
    seed_dir.mkdir(parents=True, exist_ok=True)
    if not same_filesystem(source, seed_dir):
        raise LinkError(
            f"发种目录 {seed_dir} 和 {source} 不在同一个文件系统，无法建立硬链接。"
            "请把发种目录设在下载目录所在的分区上（Docker 中两者要在同一个挂载卷里，不能分开挂载）。"
        )
    target = seed_dir / target_name(source, name)
    if target.exists() or target.is_symlink():
        if _same_tree(source, target):
            return target, False
        raise LinkError(f"发种目录中已有 {target.name}，但和 {source} 不是同一份数据。请换一个发种名称，或先删除它。")

    staging = Path(tempfile.mkdtemp(prefix=".whatdvd-link-", dir=seed_dir))
    try:
        built = staging / target.name
        if source.is_file():
            os.link(source, built)
        else:
            for relative in _files(source):
                (built / relative).parent.mkdir(parents=True, exist_ok=True)
                os.link(source / relative, built / relative)
            built.mkdir(exist_ok=True)  # 空目录
        try:
            built.rename(target)
        except OSError:
            if target.exists() and _same_tree(source, target):  # 同时运行的另一个任务刚建好
                return target, False
            raise
    except OSError as error:
        if error.errno == errno.EXDEV:
            raise LinkError(
                f"{source} 和发种目录不在同一个文件系统（或分属不同的挂载卷），无法建立硬链接。"
            ) from None
        raise LinkError(f"建立硬链接失败：{error}") from None
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target, True
