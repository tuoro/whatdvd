"""命令行入口。"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from .dvd import ScanError
from .naming import clean_title
from .pipeline import Analysis, DiscOutput, analyze, generate
from .post import DEFAULT_TEMPLATE, PostDisc, TemplateError, render_post
from .probe import ProbeError
from .runner import CommandError, Runner, SubprocessRunner
from .sources import find_sources, is_iso, open_disc
from .torrent import DEFAULT_PIECE_LENGTH, PIECE_LENGTH_RANGE, make_torrent
from .upload import PIXHOST_DOMAINS, Pixhost, upload_all

MEDIA_TOOLS = ("ffmpeg", "ffprobe", "mediainfo")
DEFAULT_OUTPUT = Path("whatdvd-output")


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB"):
        if value < 1024:
            return f"{size} B" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} GiB"


def output_title(path: Path) -> str:
    """发布说明的文件名前缀：输入路径最后一级（ISO 去掉扩展名），清理后使用。"""
    path = path.absolute()
    return clean_title(path.stem if path.is_file() else path.name)


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("必须是正整数")
    return value


def _piece_length(text: str) -> int:
    value = int(text)
    if value not in PIECE_LENGTH_RANGE:
        raise argparse.ArgumentTypeError(f"必须在 {PIECE_LENGTH_RANGE.start}–{PIECE_LENGTH_RANGE.stop - 1} 之间")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="whatdvd", description="DVD 发种助手")
    commands = parser.add_subparsers(dest="command", required=True)
    path_help = "ISO、VIDEO_TS、盘目录，或包含多张盘的目录"

    scan = commands.add_parser("scan", help="识别 DVD，显示选中的文件、容量、制式和截图尺寸")
    scan.add_argument("path", type=Path, help=path_help)
    scan.add_argument("--temp-dir", type=Path, help="ISO 解包用的临时目录（默认系统临时目录）")

    run = commands.add_parser("run", help="生成截图和 MediaInfo，上传截图并生成发布说明")
    run.add_argument("path", type=Path, help=path_help)
    run.add_argument("-n", "--count", type=_positive_int, default=10, help="每张盘的截图数量（默认 10）")
    run.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT, help="输出目录（默认 ./whatdvd-output）")
    run.add_argument("--temp-dir", type=Path, help="ISO 解包用的临时目录（默认系统临时目录）")
    run.add_argument("--no-upload", action="store_true", help="不上传截图，也不生成发布说明")
    run.add_argument("--pixhost-domain", choices=PIXHOST_DOMAINS, default=PIXHOST_DOMAINS[0], help="Pixhost 域名")
    run.add_argument("--proxy", help="上传图床用的 HTTP 代理，例如 http://127.0.0.1:7890")
    run.add_argument("--template", type=Path, help="发布说明模板文件，可用 $name、$mediainfo、$screenshots")

    torrent = commands.add_parser("torrent", help="用 mktorrent 生成 private 种子")
    torrent.add_argument("path", type=Path, help="要做种的文件夹或 ISO")
    torrent.add_argument("-a", "--announce", action="append", default=[], help="Tracker 地址，可重复；不填则不写 announce")
    torrent.add_argument(
        "-l", "--piece-length", type=_piece_length, default=DEFAULT_PIECE_LENGTH,
        help=f"分块大小，2 的幂（默认 {DEFAULT_PIECE_LENGTH}，即 16 MiB）",
    )
    torrent.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT, help="输出目录（默认 ./whatdvd-output）")
    return parser


def _missing(runner: Runner, tools: Sequence[str]) -> bool:
    missing = [tool for tool in tools if runner.which(tool) is None]
    if missing:
        print(f"缺少外部命令：{'、'.join(missing)}，请先安装。", file=sys.stderr)
    return bool(missing)


def _print_analysis(source: Path, analysis: Analysis) -> None:
    disc = analysis.disc
    video = analysis.video
    lines = [
        f"[{disc.name}]",
        f"  来源：{source}",
        f"  VOB：{disc.vob.name}（{format_bytes(disc.vob.stat().st_size)}）",
        f"  IFO：{disc.ifo.name if disc.ifo else '无'}",
        f"  容量：{disc.media_type}，共 {format_bytes(disc.total_bytes)}",
        f"  制式：{analysis.standard or f'未知（高度 {video.height}）'}",
        f"  截图尺寸：{video.width}x{video.height}，PAR {video.par_text} → {analysis.size[0]}x{analysis.size[1]}",
        f"  VOB 时长：{analysis.duration} 秒",
    ]
    print("\n".join(lines))


def _print_progress(message: str) -> None:
    print(f"  {message}")


def _discs(args: argparse.Namespace, runner: Runner) -> tuple[list[tuple[str, DiscOutput]], bool]:
    """逐张盘识别；run 命令同时生成截图和 MediaInfo。返回 (盘名, 输出) 列表和是否全部成功。"""
    sources = find_sources(args.path)
    if any(is_iso(source) for source in sources) and _missing(runner, ["7z"]):
        raise ScanError("处理 ISO 需要 7z")

    outputs: list[tuple[str, DiscOutput]] = []
    all_ok = True
    for source in sources:
        label = source.name if is_iso(source) else source.parent.name
        try:
            with open_disc(runner, source, args.path, args.temp_dir) as disc:
                analysis = analyze(runner, disc)
                _print_analysis(source, analysis)
                if args.command == "run":
                    output = generate(runner, analysis, count=args.count, output_dir=args.output, progress=_print_progress)
                    outputs.append((disc.name, output))
                    if output.failed:
                        all_ok = False
                        print(f"  有 {len(output.failed)} 张截图失败。", file=sys.stderr)
        except (ScanError, ProbeError, CommandError, OSError) as error:
            print(f"[{label}] 失败：{error}", file=sys.stderr)
            all_ok = False
    return outputs, all_ok


def _upload_and_post(args: argparse.Namespace, outputs: list[tuple[str, DiscOutput]], template: str) -> bool:
    """截图全部上传成功才生成发布说明。"""
    print("[上传截图到 Pixhost]")
    host = Pixhost(args.pixhost_domain, proxy=args.proxy)
    discs: list[PostDisc] = []
    all_uploaded = True
    try:
        for name, output in outputs:
            results = upload_all(host, [shot.path for shot in output.shots if shot.ok], _print_progress)
            all_uploaded = all_uploaded and all(result.image for result in results)
            urls = [result.image.direct_url for result in results if result.image]
            discs.append(PostDisc(name=name, mediainfo=output.mediainfo.read_text(encoding="utf-8"), image_urls=urls))
    finally:
        host.close()

    if not all_uploaded:
        print("有截图上传失败，未生成发布说明。请重新运行。", file=sys.stderr)
        return False
    post_path = args.output / f"{output_title(args.path)}.post.txt"
    post_path.write_text(render_post(discs, template), encoding="utf-8")
    print(f"发布说明：{post_path}")
    return True


def _cmd_media(args: argparse.Namespace, runner: Runner) -> int:
    if _missing(runner, MEDIA_TOOLS):
        return 2
    template = DEFAULT_TEMPLATE
    if args.command == "run" and args.template is not None:
        try:
            template = args.template.read_text(encoding="utf-8")
            render_post([PostDisc(name="", mediainfo="", image_urls=[])], template)
        except (OSError, TemplateError) as error:
            print(error, file=sys.stderr)
            return 2

    try:
        outputs, all_ok = _discs(args, runner)
    except ScanError as error:
        print(error, file=sys.stderr)
        return 2

    if args.command == "run" and outputs and not args.no_upload:
        all_ok = _upload_and_post(args, outputs, template) and all_ok
    return 0 if all_ok else 1


def _cmd_torrent(args: argparse.Namespace, runner: Runner) -> int:
    if _missing(runner, ["mktorrent"]):
        return 2
    source = args.path.absolute()
    if not source.exists():
        print(f"路径不存在：{source}", file=sys.stderr)
        return 2
    started = time.monotonic()
    try:
        output = make_torrent(runner, source, args.output, announces=args.announce, piece_length=args.piece_length)
    except CommandError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"种子：{output}（用时 {time.monotonic() - started:.0f} 秒）")
    return 0


def main(argv: Sequence[str] | None = None, runner: Runner | None = None) -> int:
    args = _parser().parse_args(argv)
    runner = runner or SubprocessRunner()
    if args.command == "torrent":
        return _cmd_torrent(args, runner)
    return _cmd_media(args, runner)


if __name__ == "__main__":
    sys.exit(main())
