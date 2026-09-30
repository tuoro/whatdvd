"""截图：取点同 Upload-Assistant（时长的 5%–90% 均匀分布），命令同 jietu。画面除比例换算外不做任何处理。"""

from __future__ import annotations

from pathlib import Path

from .runner import Runner


# 取点范围同 Upload-Assistant：避开开头 5%（片头 logo）和最后 10%（片尾字幕）
START_RATIO = 0.05
END_RATIO = 0.90


def usable_range(duration: int) -> tuple[int, int]:
    """可取点的范围（秒）：时长的 5%–90%。"""
    return int(duration * START_RATIO), int(duration * END_RATIO)


def timestamps(duration: int, count: int) -> list[int]:
    """同 Upload-Assistant：从 5% 处开始，每隔 (90% − 5%) ÷ 张数 取一张，最后一张落在 90% 之前。"""
    start, end = usable_range(duration)
    interval = (end - start) // count if count > 1 else end - start
    return [start + i * interval for i in range(count)]


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
