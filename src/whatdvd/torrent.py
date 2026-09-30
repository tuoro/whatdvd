"""做种，参数同 zuozhong：mktorrent -v -p -l 24。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .naming import clean_title
from .runner import Runner

DEFAULT_PIECE_LENGTH = 24
"""分块大小为 2 的幂，24 即 16 MiB。"""

PIECE_LENGTH_RANGE = range(15, 29)
"""mktorrent 1.1 接受的范围：32 KiB 到 256 MiB。"""


def torrent_name(source: Path) -> str:
    """同 zuozhong：取路径最后一级，清理后加 .torrent。"""
    return f"{clean_title(source.name)}.torrent"


def make_torrent(
    runner: Runner,
    source: Path,
    output_dir: Path,
    *,
    announces: Sequence[str] = (),
    piece_length: int = DEFAULT_PIECE_LENGTH,
) -> Path:
    """生成 private 种子。没有 announce 时不加 -a（zuozhong 的“空 announce”选项即此意）。"""
    if piece_length not in PIECE_LENGTH_RANGE:
        raise ValueError(f"分块大小必须在 {PIECE_LENGTH_RANGE.start}–{PIECE_LENGTH_RANGE.stop - 1} 之间：{piece_length}")
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / torrent_name(source)
    output.unlink(missing_ok=True)  # mktorrent 不会覆盖已有文件

    args: list[str | Path] = ["mktorrent", "-v", "-p", "-l", str(piece_length)]
    for announce in announces:
        args += ["-a", announce]
    args += ["-o", output, source]
    runner.run(args)
    return output
