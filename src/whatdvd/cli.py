"""命令行入口。"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from .dvd import ScanError, find_video_ts_dirs, scan_disc
from .pipeline import Analysis, analyze, generate
from .probe import ProbeError
from .runner import CommandError, SubprocessRunner

REQUIRED_TOOLS = ("ffmpeg", "ffprobe", "mediainfo")


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB"):
        if value < 1024:
            return f"{size} B" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} GiB"


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("必须是正整数")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="whatdvd", description="DVD 发种助手")
    commands = parser.add_subparsers(dest="command", required=True)

    scan = commands.add_parser("scan", help="识别 DVD，显示选中的文件、容量、制式和截图尺寸")
    scan.add_argument("path", type=Path, help="VIDEO_TS、盘目录，或包含多张盘的目录")

    run = commands.add_parser("run", help="生成截图和 MediaInfo")
    run.add_argument("path", type=Path, help="VIDEO_TS、盘目录，或包含多张盘的目录")
    run.add_argument("-n", "--count", type=_positive_int, default=10, help="每张盘的截图数量（默认 10）")
    run.add_argument("-o", "--output", type=Path, default=Path("whatdvd-output"), help="输出目录（默认 ./whatdvd-output）")
    return parser


def _print_analysis(analysis: Analysis) -> None:
    disc = analysis.disc
    video = analysis.video
    lines = [
        f"[{disc.root.name}]",
        f"  VIDEO_TS：{disc.video_ts}",
        f"  VOB：{disc.vob.name}（{format_bytes(disc.vob.stat().st_size)}）",
        f"  IFO：{disc.ifo.name if disc.ifo else '无'}",
        f"  容量：{disc.media_type}，共 {format_bytes(disc.total_bytes)}",
        f"  制式：{analysis.standard or f'未知（高度 {video.height}）'}",
        f"  截图尺寸：{video.width}x{video.height}，PAR {video.par_text} → {analysis.size[0]}x{analysis.size[1]}",
        f"  VOB 时长：{analysis.duration} 秒",
    ]
    print("\n".join(lines))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runner = SubprocessRunner()

    missing = [tool for tool in REQUIRED_TOOLS if runner.which(tool) is None]
    if missing:
        print(f"缺少外部命令：{'、'.join(missing)}，请先安装。", file=sys.stderr)
        return 2

    try:
        video_ts_dirs = find_video_ts_dirs(args.path)
    except ScanError as error:
        print(error, file=sys.stderr)
        return 2

    # 同 jietu：MediaInfo 中删除输入路径的上级目录前缀。
    strip_prefix = os.path.join(str(args.path.absolute().parent), "")

    all_ok = True
    for video_ts in video_ts_dirs:
        try:
            analysis = analyze(runner, scan_disc(video_ts))
        except (ScanError, ProbeError, CommandError) as error:
            print(f"[{video_ts.parent.name}] 识别失败：{error}", file=sys.stderr)
            all_ok = False
            continue

        _print_analysis(analysis)
        if args.command != "run":
            continue

        try:
            output = generate(
                runner,
                analysis,
                count=args.count,
                output_dir=args.output,
                strip_prefix=strip_prefix,
                progress=lambda message: print(f"  {message}"),
            )
        except (CommandError, OSError) as error:
            print(f"[{video_ts.parent.name}] 生成失败：{error}", file=sys.stderr)
            all_ok = False
            continue
        if output.failed:
            all_ok = False
            print(f"  有 {len(output.failed)} 张截图失败。", file=sys.stderr)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
