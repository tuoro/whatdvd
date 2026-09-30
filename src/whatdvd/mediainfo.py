"""MediaInfo 报告，格式同 jietu：VOB 在前，IFO 在后，写进同一个文件。"""

from __future__ import annotations

from pathlib import Path

from .runner import Runner


def strip_path_prefix(text: str, prefix: str) -> str:
    """同 jietu 的 `sed "s|${FileLoc}/||"`：每行只删除第一处。"""
    return "\n".join(line.replace(prefix, "", 1) for line in text.split("\n"))


def build_report(runner: Runner, vob: Path, ifo: Path | None, strip_prefix: str) -> str:
    report = runner.run(["mediainfo", vob]).stdout
    if ifo is not None:
        # jietu 用 `echo -e "\n\n"` 分隔，写出的是三个换行符。
        report += "\n\n\n" + runner.run(["mediainfo", ifo]).stdout
    return strip_path_prefix(report, strip_prefix)
