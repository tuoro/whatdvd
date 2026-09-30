"""Web 服务的配置：TOML 文件 + 命令行覆盖。"""

from __future__ import annotations

import os
import secrets
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..post import DEFAULT_TEMPLATE, TemplateError, load_template
from ..torrent import DEFAULT_PIECE_LENGTH, PIECE_LENGTH_RANGE
from ..upload import PIXHOST_DOMAINS

DEFAULT_CONFIG_PATH = Path("~/.config/whatdvd/config.toml")
DEFAULT_OUTPUT_DIR = Path("~/.local/share/whatdvd/output")
DEFAULT_PORT = 26873
TOKEN_ENV = "WHATDVD_TOKEN"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ServerConfig:
    roots: tuple[Path, ...]
    """允许在界面中浏览和处理的目录（已解析符号链接）。"""
    output_dir: Path
    token: str
    token_generated: bool = False
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    max_jobs: int = 1
    temp_dir: Path | None = None
    screenshot_count: int = 10
    pixhost_domain: str = PIXHOST_DOMAINS[0]
    proxy: str | None = None
    announces: tuple[str, ...] = ()
    piece_length: int = DEFAULT_PIECE_LENGTH
    template: str = DEFAULT_TEMPLATE


# 允许的键与类型：(表名, 键名) → 类型。表名为空表示顶层。
_SCHEMA: dict[tuple[str, str], type | tuple[type, ...]] = {
    ("", "host"): str,
    ("", "port"): int,
    ("", "token"): str,
    ("", "roots"): list,
    ("", "output_dir"): str,
    ("", "temp_dir"): str,
    ("", "max_jobs"): int,
    ("screenshots", "count"): int,
    ("pixhost", "domain"): str,
    ("pixhost", "proxy"): str,
    ("torrent", "announces"): list,
    ("torrent", "piece_length"): int,
    ("post", "template"): str,
}


def _flatten(data: dict[str, Any]) -> dict[tuple[str, str], Any]:
    tables = {table for table, _ in _SCHEMA if table}
    flat: dict[tuple[str, str], Any] = {}
    for key, value in data.items():
        if key in tables:
            if not isinstance(value, dict):
                raise ConfigError(f"配置项 [{key}] 应该是一个表")
            flat.update({(key, sub): sub_value for sub, sub_value in value.items()})
        else:
            flat[("", key)] = value
    for (table, key), value in flat.items():
        name = f"{table}.{key}" if table else key
        expected = _SCHEMA.get((table, key))
        if expected is None:
            raise ConfigError(f"未知的配置项：{name}")
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ConfigError(f"配置项 {name} 的类型不对")
    return flat


def _expand(text: str) -> Path:
    return Path(text).expanduser()


def _read(path: Path | None) -> dict[tuple[str, str], Any]:
    if path is None:
        path = DEFAULT_CONFIG_PATH.expanduser()
        if not path.is_file():
            return {}
    try:
        with path.expanduser().open("rb") as handle:
            return _flatten(tomllib.load(handle))
    except OSError as error:
        raise ConfigError(f"无法读取配置文件：{error}") from None
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"配置文件格式有误：{error}") from None


def load_config(
    path: Path | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    roots: Sequence[Path] = (),
) -> ServerConfig:
    flat = _read(path)

    raw_roots = [Path(p) for p in roots] or [_expand(str(p)) for p in flat.get(("", "roots"), [])]
    if not raw_roots:
        raise ConfigError("至少需要一个允许浏览的目录：在配置文件中设置 roots，或使用 --root")
    resolved_roots: list[Path] = []
    for root in raw_roots:
        if not root.is_dir():
            raise ConfigError(f"roots 中的目录不存在：{root}")
        resolved_roots.append(root.resolve())

    token = flat.get(("", "token")) or os.environ.get(TOKEN_ENV, "")
    token_generated = not token
    if token_generated:
        token = secrets.token_urlsafe(24)

    port = port if port is not None else flat.get(("", "port"), DEFAULT_PORT)
    if not 1 <= port <= 65535:
        raise ConfigError(f"端口无效：{port}")

    max_jobs = flat.get(("", "max_jobs"), 1)
    count = flat.get(("screenshots", "count"), 10)
    if max_jobs < 1 or count < 1:
        raise ConfigError("max_jobs 和 screenshots.count 必须是正整数")

    domain = flat.get(("pixhost", "domain"), PIXHOST_DOMAINS[0])
    if domain not in PIXHOST_DOMAINS:
        raise ConfigError(f"pixhost.domain 只能是 {' 或 '.join(PIXHOST_DOMAINS)}")

    piece_length = flat.get(("torrent", "piece_length"), DEFAULT_PIECE_LENGTH)
    if piece_length not in PIECE_LENGTH_RANGE:
        raise ConfigError(f"torrent.piece_length 必须在 {PIECE_LENGTH_RANGE.start}–{PIECE_LENGTH_RANGE.stop - 1} 之间")
    announces = flat.get(("torrent", "announces"), [])
    if not all(isinstance(a, str) and a.strip() for a in announces):
        raise ConfigError("torrent.announces 必须是非空字符串列表")

    template_path = flat.get(("post", "template"))
    try:
        template = load_template(_expand(template_path) if template_path else None)
    except (OSError, TemplateError) as error:
        raise ConfigError(f"发布说明模板有误：{error}") from None

    temp_dir = flat.get(("", "temp_dir"))
    return ServerConfig(
        roots=tuple(resolved_roots),
        output_dir=_expand(flat.get(("", "output_dir"), str(DEFAULT_OUTPUT_DIR))).absolute(),
        token=token,
        token_generated=token_generated,
        host=host or flat.get(("", "host"), "127.0.0.1"),
        port=port,
        max_jobs=max_jobs,
        temp_dir=_expand(temp_dir) if temp_dir else None,
        screenshot_count=count,
        pixhost_domain=domain,
        proxy=flat.get(("pixhost", "proxy")) or None,
        announces=tuple(a.strip() for a in announces),
        piece_length=piece_length,
        template=template,
    )
