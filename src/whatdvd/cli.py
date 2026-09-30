"""命令行入口。"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from .post import TemplateError, load_template
from .runner import CommandError, Runner, SubprocessRunner
from .torrent import DEFAULT_PIECE_LENGTH, PIECE_LENGTH_RANGE, make_torrent
from .upload import PIXHOST_DOMAINS, Pixhost
from .workflow import RunOptions, WorkflowError, check_tools, format_bytes, output_title, run

__all__ = ["format_bytes", "main", "output_title"]

DEFAULT_OUTPUT = Path("whatdvd-output")


class ConsoleReporter:
    def info(self, message: str) -> None:
        print(message)

    def error(self, message: str) -> None:
        print(message, file=sys.stderr)

    def progress(self, done: int, total: int) -> None:
        pass  # 命令行逐行输出，已能看出进度


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

    run_cmd = commands.add_parser("run", help="生成截图和 MediaInfo，上传截图并生成发布说明")
    run_cmd.add_argument("path", type=Path, help=path_help)
    run_cmd.add_argument("-n", "--count", type=_positive_int, default=10, help="每张盘的截图数量（默认 10）")
    run_cmd.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT, help="输出目录（默认 ./whatdvd-output）")
    run_cmd.add_argument("--temp-dir", type=Path, help="ISO 解包用的临时目录（默认系统临时目录）")
    run_cmd.add_argument("--no-upload", action="store_true", help="不上传截图，也不生成发布说明")
    run_cmd.add_argument("--pixhost-domain", choices=PIXHOST_DOMAINS, default=PIXHOST_DOMAINS[0], help="Pixhost 域名")
    run_cmd.add_argument("--proxy", help="上传图床用的 HTTP 代理，例如 http://127.0.0.1:7890")
    run_cmd.add_argument("--template", type=Path, help="发布说明模板文件，可用 $name、$mediainfo、$screenshots")

    torrent = commands.add_parser("torrent", help="用 mktorrent 生成 private 种子")
    torrent.add_argument("path", type=Path, help="要做种的文件夹或 ISO")
    torrent.add_argument("-a", "--announce", action="append", default=[], help="Tracker 地址，可重复；不填则不写 announce")
    torrent.add_argument(
        "-l", "--piece-length", type=_piece_length, default=DEFAULT_PIECE_LENGTH,
        help=f"分块大小，2 的幂（默认 {DEFAULT_PIECE_LENGTH}，即 16 MiB）",
    )
    torrent.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT, help="输出目录（默认 ./whatdvd-output）")

    serve = commands.add_parser("serve", help="启动 Web 界面")
    serve.add_argument("-c", "--config", type=Path, help="配置文件（默认 ~/.config/whatdvd/config.toml）")
    serve.add_argument("--host", help="监听地址，覆盖配置文件")
    serve.add_argument("--port", type=int, help="监听端口，覆盖配置文件")
    serve.add_argument("--root", type=Path, action="append", default=[], help="允许浏览的目录，可重复，覆盖配置文件")

    token = commands.add_parser("token", help="显示 Web 界面的登录 token 和登录链接")
    token.add_argument("-c", "--config", type=Path, help="配置文件（默认 ~/.config/whatdvd/config.toml）")
    return parser


def _cmd_media(args: argparse.Namespace, runner: Runner) -> int:
    is_run = args.command == "run"
    try:
        template = load_template(args.template if is_run else None)
    except (OSError, TemplateError) as error:
        print(error, file=sys.stderr)
        return 2

    options = RunOptions(
        output_dir=args.output if is_run else DEFAULT_OUTPUT,
        count=args.count if is_run else 10,
        temp_dir=args.temp_dir,
        generate=is_run,
        upload=is_run and not args.no_upload,
        template=template,
    )
    try:
        result = run(
            runner,
            args.path,
            options,
            ConsoleReporter(),
            lambda: Pixhost(args.pixhost_domain, proxy=args.proxy),
        )
    except WorkflowError as error:
        print(error, file=sys.stderr)
        return 2
    return 0 if result.ok else 1


def _cmd_torrent(args: argparse.Namespace, runner: Runner) -> int:
    try:
        check_tools(runner, ["mktorrent"])
    except WorkflowError as error:
        print(error, file=sys.stderr)
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


def _cmd_serve(args: argparse.Namespace) -> int:
    from .web.config import ConfigError, load_config
    from .web.server import serve

    try:
        config = load_config(args.config, host=args.host, port=args.port, roots=args.root)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    serve(config)
    return 0


def _cmd_token(args: argparse.Namespace) -> int:
    from .web.config import ConfigError, read_token
    from .web.server import login_url

    try:
        token, host, port = read_token(args.config)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    source = {
        "config": "配置文件中的 token",
        "env": "环境变量 WHATDVD_TOKEN",
        "file": f"保存在 {token.file}",
        "new": f"刚刚生成，已保存到 {token.file}",
    }[token.source]
    print(f"token：{token.token}（{source}）")
    print(f"登录链接：{login_url(host, port)}?token={token.token}")
    return 0


def main(argv: Sequence[str] | None = None, runner: Runner | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve":
        return _cmd_serve(args)
    if args.command == "token":
        return _cmd_token(args)
    runner = runner or SubprocessRunner()
    if args.command == "torrent":
        return _cmd_torrent(args, runner)
    return _cmd_media(args, runner)


if __name__ == "__main__":
    sys.exit(main())
