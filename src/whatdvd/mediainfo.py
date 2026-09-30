"""MediaInfo 报告，格式同 jietu：VOB 在前，IFO 在后，写进同一个文件。

jietu 事后用 sed 删除路径前缀；这里改为在基准目录下用相对路径运行 mediainfo，
效果相同（Complete name 显示为 <盘名>/VIDEO_TS/…），输出保持未经修改。
"""

from __future__ import annotations

import os
from pathlib import Path

from .runner import Runner


def relative_arg(path: Path, root: Path) -> str:
    """path 在 root 之下时返回相对路径，否则返回原路径；以 "-" 开头时加 "./"，避免被当成选项。"""
    arg = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    return f"./{arg}" if arg.startswith("-") else arg


def mediainfo(runner: Runner, path: Path, root: Path) -> str:
    return runner.run(["mediainfo", relative_arg(path, root)], cwd=root).stdout


def build_report(runner: Runner, vob: Path, ifo: Path | None, root: Path) -> str:
    report = mediainfo(runner, vob, root)
    if ifo is not None:
        # jietu 用 `echo -e "\n\n"` 分隔，写出的是三个换行符。
        report += "\n\n\n" + mediainfo(runner, ifo, root)
    return report
