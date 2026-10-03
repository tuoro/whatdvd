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


def split_report(report: str) -> tuple[str, str | None]:
    """build_report 的输出拆回（VOB 的 MediaInfo，IFO 的 MediaInfo）：IFO 部分从 Complete name 为 .IFO 的
    General 段开始；没有 IFO 部分时第二项为 None。"""
    lines = report.split("\n")
    for index, line in enumerate(lines):
        if line.strip() == "General" and index + 1 < len(lines):
            name = lines[index + 1]
            if name.lstrip().startswith("Complete name") and name.rstrip().upper().endswith(".IFO"):
                return "\n".join(lines[:index]).rstrip() + "\n", "\n".join(lines[index:]).rstrip() + "\n"
    return report, None
