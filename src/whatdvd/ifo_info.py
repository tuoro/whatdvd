"""VIDEO_TS.IFO（VMGI_MAT）头部的盘信息，攒数据用：看重新制作过的盘是否留下可辨认的标记。

只读不判断。偏移按 DVD-Video 规范：
- 0x20 规范版本；0x23 区码掩码（某位为 1 表示不能在该区播放）；0x3E 标题集数量；
- 0x40 提供者标识（32 字节，制作时可选填，正版盘常为空或厂商名，有的制作软件会写上自己的名字）。
"""

from __future__ import annotations

import csv
import io
import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_MAGIC = b"DVDVIDEO-VMG"
_HEADER = 0x60


@dataclass(frozen=True)
class VmgInfo:
    provider: str
    """提供者标识，去掉末尾的空字节和空格；没填为空字符串。"""
    version: str
    """规范版本，例如 "1.1"。"""
    regions: str
    """可以播放的区码，例如 "2,5"；全区为 "1-8"。"""
    title_sets: int
    bup_identical: bool | None
    """VIDEO_TS.BUP 是否和 IFO 完全相同（没有 BUP 时为 None）。"""


def _find(video_ts: Path, name: str) -> Path | None:
    for item in video_ts.iterdir():
        if item.name.upper() == name and item.is_file():
            return item
    return None


def _regions(mask: int) -> str:
    playable = [str(region) for region in range(1, 9) if not mask & (1 << (region - 1))]
    return "1-8" if len(playable) == 8 else ",".join(playable)


def parse_vmg(data: bytes) -> VmgInfo | None:
    if len(data) < _HEADER or not data.startswith(_MAGIC):
        return None
    provider = data[0x40:0x60].rstrip(b"\0 ").decode("latin-1")
    version = f"{data[0x21] >> 4}.{data[0x21] & 0xF}"
    return VmgInfo(provider, version, _regions(data[0x23]), int.from_bytes(data[0x3E:0x40], "big"), None)


def read_vmg(video_ts: Path) -> VmgInfo | None:
    """VIDEO_TS 目录中 VIDEO_TS.IFO 的头部；读不到时返回 None（ISO 解包后的临时目录可能已经删掉）。"""
    try:
        ifo = _find(video_ts, "VIDEO_TS.IFO")
        if ifo is None:
            return None
        data = ifo.read_bytes()
        bup = _find(video_ts, "VIDEO_TS.BUP")
        same = bup.read_bytes() == data if bup is not None else None
    except OSError:
        return None
    info = parse_vmg(data)
    return None if info is None else VmgInfo(**{**asdict(info), "bup_identical": same})


class SampleLog:
    """处理过的盘的 IFO 信息，一行一个 JSON，追加写入；设置页面可以导出成 CSV。"""

    FIELDS = ("time", "disc", "source_title", "details_url", "media_type", "total_bytes", "title_minutes",
              "provider", "version", "regions", "title_sets", "bup_identical", "warnings")

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def append(self, sample: dict[str, Any]) -> None:
        row = {key: sample.get(key) for key in self.FIELDS} | {"time": sample.get("time") or time.time()}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def rows(self) -> list[dict[str, Any]]:
        """同一张盘处理过多次时只留最后一次。"""
        if not self.path.is_file():
            return []
        latest: dict[tuple[Any, ...], dict[str, Any]] = {}
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                latest[(row.get("disc"), row.get("total_bytes"))] = row
        return list(latest.values())

    def csv(self) -> str:
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(self.FIELDS)
        for row in self.rows():
            values = []
            for key in self.FIELDS:
                value = row.get(key)
                if key == "time" and isinstance(value, (int, float)):
                    value = time.strftime("%Y-%m-%d %H:%M", time.localtime(value))
                elif isinstance(value, list):
                    value = " / ".join(str(v) for v in value)
                values.append("" if value is None else value)
            writer.writerow(values)
        return "﻿" + out.getvalue()  # 带 BOM：Excel 打开中文不乱码
