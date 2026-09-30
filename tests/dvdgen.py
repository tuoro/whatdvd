"""生成测试用的小型 DVD 结构（ffmpeg 测试画面 + dvdauthor），不含任何真实影片。

也可以直接运行：python tests/dvdgen.py 输出目录
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

TOOLS = ("ffmpeg", "ffprobe", "mediainfo", "dvdauthor")

_FORMATS = {
    "PAL": {"size": "720x576", "rate": "25", "target": "pal-dvd"},
    "NTSC": {"size": "720x480", "rate": "30000/1001", "target": "ntsc-dvd"},
}


@dataclass(frozen=True)
class Title:
    seconds: int
    aspect: str = "16:9"
    display_width: int | None = None
    """改写 MPEG-2 sequence display extension 中的显示宽度，例如 PAL 16:9 盘常见的 540（pan & scan）。"""


def set_display_width(mpg: Path, width: int) -> None:
    """改写所有 sequence display extension 的 display_horizontal_size（14 位）。"""
    data = bytearray(mpg.read_bytes())
    index = 0
    while (index := data.find(b"\x00\x00\x01\xb5", index)) >= 0:
        first = data[index + 4]
        if first >> 4 == 2:  # sequence display extension
            pos = index + 5 + (3 if first & 1 else 0)  # 跳过 colour description
            bits = int.from_bytes(data[pos : pos + 4], "big")
            data[pos : pos + 4] = ((bits & ((1 << 18) - 1)) | (width << 18)).to_bytes(4, "big")
        index += 4
    mpg.write_bytes(data)


def missing_tools() -> list[str]:
    return [tool for tool in TOOLS if shutil.which(tool) is None]


def make_disc(disc_dir: Path, standard: str, titles: Sequence[Title], work_dir: Path) -> Path:
    """每个 Title 生成一个 titleset（VTS_01、VTS_02……），返回 VIDEO_TS 路径。"""
    fmt = _FORMATS[standard]
    env = {**os.environ, "VIDEO_FORMAT": standard}
    work_dir.mkdir(parents=True, exist_ok=True)
    disc_dir.parent.mkdir(parents=True, exist_ok=True)  # dvdauthor 只创建最后一级目录
    for number, title in enumerate(titles, start=1):
        mpg = work_dir / f"{disc_dir.name}-{number}.mpg"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"testsrc2=size={fmt['size']}:rate={fmt['rate']}",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                "-t", str(title.seconds),
                "-target", fmt["target"], "-aspect", title.aspect, "-b:v", "1500k",
                *(["-seq_disp_ext", "always"] if title.display_width else []),
                mpg,
            ],
            check=True,
        )
        if title.display_width:
            set_display_width(mpg, title.display_width)
        subprocess.run(["dvdauthor", "-o", disc_dir, "-t", mpg], check=True, env=env, capture_output=True)
    subprocess.run(["dvdauthor", "-o", disc_dir, "-T"], check=True, env=env, capture_output=True)
    return disc_dir / "VIDEO_TS"


def make_iso(disc_dir: Path, iso: Path) -> Path:
    """用 genisoimage 打包成 DVD-Video ISO（UDF + ISO 9660）。"""
    subprocess.run(
        ["genisoimage", "-quiet", "-dvd-video", "-V", "WHATDVD", "-o", iso, disc_dir],
        check=True,
        capture_output=True,
    )
    return iso


def make_sample_set(root: Path) -> Path:
    """一部两张盘的样例：盘 1 正片在 VTS_02（PAL 16:9，显示区域标为 540 宽），盘 2 为 NTSC 4:3。返回影片目录。"""
    movie = root / "Sample Movie (2001)"
    work = root / "work"
    make_disc(movie / "Disc 1", "PAL", [Title(8, "4:3"), Title(70, "16:9", display_width=540)], work)
    make_disc(movie / "Disc 2", "NTSC", [Title(25, "4:3")], work)
    shutil.rmtree(work)
    return movie


if __name__ == "__main__":
    if missing := missing_tools():
        sys.exit(f"缺少外部命令：{'、'.join(missing)}")
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "sample-dvd")
    print(make_sample_set(target))
