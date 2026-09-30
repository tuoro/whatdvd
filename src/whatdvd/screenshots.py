"""截图，取点和命令同 jietu。画面除比例换算外不做任何处理。"""

from __future__ import annotations

from pathlib import Path

from .runner import Runner


def step_seconds(duration: int) -> int:
    """按时长分档的取点间隔（秒）。"""
    if duration >= 3600:
        return 331
    if duration >= 1500:
        return 121
    if duration >= 600:
        return 71
    return 21


def timestamps(duration: int, count: int) -> list[int]:
    """第 k 张截图取在 k × 间隔 秒处，k 从 1 开始。"""
    step = step_seconds(duration)
    return [step * k for k in range(1, count + 1)]


def format_timestamp(seconds: int) -> str:
    """同 `date -u -d @秒 +%H:%M:%S`。"""
    seconds %= 86400
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def capture(runner: Runner, vob: Path, at: int, size: tuple[int, int], output: Path) -> bool:
    """先用输入端 -ss 快速定位，再用输出端 -ss 向后解码 1 秒取一帧。以输出文件是否生成判断成功。"""
    output.unlink(missing_ok=True)
    runner.run(
        [
            "ffmpeg",
            "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", format_timestamp(at),
            "-i", vob,
            "-ss", "00:00:01",
            "-frames:v", "1",
            "-s", f"{size[0]}x{size[1]}",
            output,
        ],
        check=False,
    )
    return output.is_file()


def compress(runner: Runner, png: Path) -> bool:
    """用 nconvert 无损重新压缩，同 jietu。调用方负责确认 nconvert 已安装。"""
    temp = png.with_name(f"{png.stem}_1.png")
    result = runner.run(
        ["nconvert", "-out", "png", "-clevel", "6", "-o", temp, png],
        check=False,
    )
    if result.returncode == 0 and temp.is_file():
        temp.replace(png)
        return True
    temp.unlink(missing_ok=True)
    return False
