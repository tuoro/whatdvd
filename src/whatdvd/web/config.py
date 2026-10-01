"""Web 服务的配置：TOML 文件 + 命令行覆盖。"""

from __future__ import annotations

import json
import os
import secrets
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..post import DEFAULT_TEMPLATE, PostDisc, TemplateError, load_template, render_post
from ..resolution import ASPECT_MODES
from ..torrent import DEFAULT_PIECE_LENGTH, PIECE_LENGTH_RANGE
from ..upload import PIXHOST_DOMAINS

DEFAULT_CONFIG_PATH = Path("~/.config/whatdvd/config.toml")
DEFAULT_TOKEN_FILE = Path("~/.local/share/whatdvd/token")
CONFIG_ENV = "WHATDVD_CONFIG"
DEFAULT_OUTPUT_DIR = Path("~/.local/share/whatdvd/output")
DEFAULT_DATABASE = Path("~/.local/share/whatdvd/whatdvd.db")
DEFAULT_SETTINGS_FILE = Path("~/.local/share/whatdvd/settings.json")
QB_PASSWORD_ENV = "WHATDVD_QB_PASSWORD"
JACKETT_KEY_ENV = "WHATDVD_JACKETT_API_KEY"
DEFAULT_PORT = 26873
TOKEN_ENV = "WHATDVD_TOKEN"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class QbitConfig:
    url: str
    username: str = ""
    password: str = ""
    category: str = "whatdvd"
    save_path: str | None = None
    """qB 中的保存路径；不填用分类或 qB 的默认路径。"""
    path_map: tuple[tuple[str, str], ...] = ()
    """qB 中的路径 → whatdvd 看到的路径。"""
    interval: int = 60
    """检查下载进度的间隔（秒）。"""


@dataclass(frozen=True)
class JackettConfig:
    url: str
    api_key: str
    indexer: str = "all"
    """Jackett 中的站点 ID，"all" 为全部已配置的站点。"""
    queries: tuple[str, ...] = ("DVD9", "DVD5")
    interval: int = 60
    """自动搜索的间隔（分钟），0 为只手动刷新。"""


@dataclass(frozen=True)
class ServerConfig:
    roots: tuple[Path, ...]
    """允许在界面中浏览和处理的目录（已解析符号链接）。"""
    output_dir: Path
    token: str
    token_source: str = "config"
    """config（配置文件）、env（环境变量）、file（之前保存的）、new（这次新生成并保存的）。"""
    token_file: Path | None = None
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    max_jobs: int = 1
    temp_dir: Path | None = None
    screenshot_count: int = 10
    aspect: str = "ua"
    dark_filter: bool = True
    pixhost_domain: str = PIXHOST_DOMAINS[0]
    proxy: str | None = None
    announces: tuple[str, ...] = ()
    piece_length: int = DEFAULT_PIECE_LENGTH
    template: str = DEFAULT_TEMPLATE
    database: Path = DEFAULT_DATABASE
    """资源候选与下载状态（SQLite）。"""
    qbit: QbitConfig | None = None
    jackett: JackettConfig | None = None
    config_file: Path | None = None
    """读取的配置文件；没有时为 None。"""
    settings_file: Path = DEFAULT_SETTINGS_FILE
    """在设置页面中保存的设置（覆盖配置文件中的同名项）。"""
    overridden: frozenset[str] = frozenset()
    """被设置页面覆盖的项，例如 "screenshots.count"。"""
    template_file: str | None = None
    template_text: str | None = None


# 允许的键与类型：(表名, 键名) → 类型。表名为空表示顶层。
_SCHEMA: dict[tuple[str, str], type | tuple[type, ...]] = {
    ("", "host"): str,
    ("", "port"): int,
    ("", "token"): str,
    ("", "token_file"): str,
    ("", "roots"): list,
    ("", "output_dir"): str,
    ("", "temp_dir"): str,
    ("", "max_jobs"): int,
    ("screenshots", "count"): int,
    ("screenshots", "aspect"): str,
    ("screenshots", "dark_filter"): bool,
    ("pixhost", "domain"): str,
    ("pixhost", "proxy"): str,
    ("torrent", "announces"): list,
    ("torrent", "piece_length"): int,
    ("post", "template"): str,
    ("post", "template_text"): str,
    ("", "settings_file"): str,
    ("", "database"): str,
    ("qbittorrent", "url"): str,
    ("qbittorrent", "username"): str,
    ("qbittorrent", "password"): str,
    ("qbittorrent", "category"): str,
    ("qbittorrent", "save_path"): str,
    ("qbittorrent", "path_map"): dict,
    ("qbittorrent", "interval"): int,
    ("jackett", "url"): str,
    ("jackett", "api_key"): str,
    ("jackett", "indexer"): str,
    ("jackett", "queries"): list,
    ("jackett", "interval"): int,
}


# 可以在设置页面中修改的项。监听地址、token、roots、输出目录、数据库等只能在配置文件中设置：
# 改了需要重启，或者关系到能访问哪些文件。
EDITABLE: frozenset[tuple[str, str]] = frozenset(
    {
        ("", "max_jobs"),
        ("", "temp_dir"),
        *((table, key) for table, key in _SCHEMA if table in ("screenshots", "pixhost", "torrent", "post")),
        *((table, key) for table, key in _SCHEMA if table in ("qbittorrent", "jackett")),
    }
)
SECRETS: frozenset[tuple[str, str]] = frozenset({("qbittorrent", "password"), ("jackett", "api_key")})


def key_name(key: tuple[str, str]) -> str:
    table, name = key
    return f"{table}.{name}" if table else name


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
    _check_types(flat)
    return flat


def _check_types(flat: dict[tuple[str, str], Any]) -> None:
    for key, value in flat.items():
        name = key_name(key)
        expected = _SCHEMA.get(key)
        if expected is None:
            raise ConfigError(f"未知的配置项：{name}")
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ConfigError(f"配置项 {name} 的类型不对")


def _expand(text: str) -> Path:
    return Path(text).expanduser()


def config_path(path: Path | None) -> Path | None:
    """显式指定的路径；否则环境变量 WHATDVD_CONFIG；否则默认路径（不存在时返回 None）。"""
    if path is not None:
        return path
    if env := os.environ.get(CONFIG_ENV):
        return Path(env)
    default = DEFAULT_CONFIG_PATH.expanduser()
    return default if default.is_file() else None


@dataclass(frozen=True)
class TokenInfo:
    token: str
    source: str
    file: Path


def resolve_token(flat: dict[tuple[str, str], Any]) -> TokenInfo:
    """配置文件 > 环境变量 > 已保存的 token 文件；都没有时生成一个并保存（权限 600），之后重启不变。"""
    file = _expand(flat.get(("", "token_file"), str(DEFAULT_TOKEN_FILE))).absolute()
    if token := flat.get(("", "token"), "").strip():
        return TokenInfo(token, "config", file)
    if token := os.environ.get(TOKEN_ENV, "").strip():
        return TokenInfo(token, "env", file)
    try:
        saved = file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        saved = ""
    except OSError as error:
        raise ConfigError(f"无法读取 token 文件 {file}：{error.strerror}") from None
    if saved:
        return TokenInfo(saved, "file", file)
    token = secrets.token_urlsafe(24)
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
    except OSError as error:
        raise ConfigError(f"无法保存 token 到 {file}：{error.strerror}") from None
    return TokenInfo(token, "new", file)


def read_token(path: Path | None = None) -> tuple[TokenInfo, str, int]:
    """whatdvd token 用：只读取 token 和监听地址，不检查 roots 等其他配置。"""
    flat = _read(path)
    return resolve_token(flat), flat.get(("", "host"), "127.0.0.1"), flat.get(("", "port"), DEFAULT_PORT)


def _read(path: Path | None) -> dict[tuple[str, str], Any]:
    path = config_path(path)
    if path is None:
        return {}
    try:
        with path.expanduser().open("rb") as handle:
            return _flatten(tomllib.load(handle))
    except OSError as error:
        raise ConfigError(f"无法读取配置文件：{error}") from None
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"配置文件格式有误：{error}") from None


def read_settings(path: Path) -> dict[tuple[str, str], Any]:
    """设置页面保存的设置（JSON，结构同配置文件的表）。文件不存在时为空。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise ConfigError(f"无法读取设置文件 {path}：{error}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"设置文件格式有误：{path}")
    flat = _flatten(data)
    for key in flat:
        if key not in EDITABLE:
            raise ConfigError(f"设置文件中不能包含 {key_name(key)}，它只能在配置文件中设置")
    return flat


def write_settings(path: Path, flat: dict[tuple[str, str], Any]) -> None:
    """原子写入，权限 600（含 qB 密码和 Jackett API Key）。"""
    tables: dict[str, Any] = {}
    for (table, key), value in sorted(flat.items()):
        if table:
            tables.setdefault(table, {})[key] = value
        else:
            tables[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(tables, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp.replace(path)


def _http_url(value: str, name: str) -> str:
    if not value.startswith(("http://", "https://")):
        raise ConfigError(f"{name} 必须以 http:// 或 https:// 开头")
    return value.rstrip("/")


def _qbit_config(flat: dict[tuple[str, str], Any]) -> QbitConfig | None:
    url = flat.get(("qbittorrent", "url"), "").strip()
    if not url:
        return None
    path_map = flat.get(("qbittorrent", "path_map"), {})
    for remote, local in path_map.items():
        if not isinstance(local, str) or not remote.startswith("/") or not local.startswith("/"):
            raise ConfigError("qbittorrent.path_map 的两边都必须是绝对路径，例如 { \"/downloads\" = \"/media/downloads\" }")
    interval = flat.get(("qbittorrent", "interval"), 60)
    if interval < 10:
        raise ConfigError("qbittorrent.interval 不能小于 10 秒")
    category = flat.get(("qbittorrent", "category"), "whatdvd").strip()
    if not category:
        raise ConfigError("qbittorrent.category 不能为空")
    return QbitConfig(
        url=_http_url(url, "qbittorrent.url"),
        username=flat.get(("qbittorrent", "username"), ""),
        password=flat.get(("qbittorrent", "password"), "") or os.environ.get(QB_PASSWORD_ENV, ""),
        category=category,
        save_path=flat.get(("qbittorrent", "save_path"), "").strip() or None,
        path_map=tuple((remote.rstrip("/") or "/", local.rstrip("/") or "/") for remote, local in path_map.items()),
        interval=interval,
    )


def _jackett_config(flat: dict[tuple[str, str], Any]) -> JackettConfig | None:
    url = flat.get(("jackett", "url"), "").strip()
    if not url:
        return None
    api_key = flat.get(("jackett", "api_key"), "").strip() or os.environ.get(JACKETT_KEY_ENV, "").strip()
    if not api_key:
        raise ConfigError(f"使用 Jackett 需要 jackett.api_key（或环境变量 {JACKETT_KEY_ENV}）")
    queries = flat.get(("jackett", "queries"), ["DVD9", "DVD5"])
    if not queries or not all(isinstance(q, str) and q.strip() for q in queries):
        raise ConfigError("jackett.queries 必须是非空字符串列表")
    interval = flat.get(("jackett", "interval"), 60)
    if interval != 0 and interval < 10:
        raise ConfigError("jackett.interval 不能小于 10 分钟（0 为只手动刷新）")
    return JackettConfig(
        url=_http_url(url, "jackett.url"),
        api_key=api_key,
        indexer=flat.get(("jackett", "indexer"), "all").strip() or "all",
        queries=tuple(q.strip() for q in queries),
        interval=interval,
    )


def load_config(
    path: Path | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    roots: Sequence[Path] = (),
    settings: dict[tuple[str, str], Any] | None = None,
) -> ServerConfig:
    """settings 不为 None 时代替设置文件的内容（设置页面保存前校验用）。"""
    file = config_path(path)
    flat = _read(path)
    settings_file = _expand(flat.get(("", "settings_file"), str(DEFAULT_SETTINGS_FILE))).absolute()
    overrides = read_settings(settings_file) if settings is None else settings
    _check_types(overrides)
    flat = {**flat, **overrides}

    raw_roots = [Path(p) for p in roots] or [_expand(str(p)) for p in flat.get(("", "roots"), [])]
    if not raw_roots:
        raise ConfigError("至少需要一个允许浏览的目录：在配置文件中设置 roots，或使用 --root")
    resolved_roots: list[Path] = []
    for root in raw_roots:
        if not root.is_dir():
            raise ConfigError(f"roots 中的目录不存在：{root}")
        resolved_roots.append(root.resolve())

    token = resolve_token(flat)

    port = port if port is not None else flat.get(("", "port"), DEFAULT_PORT)
    if not 1 <= port <= 65535:
        raise ConfigError(f"端口无效：{port}")

    max_jobs = flat.get(("", "max_jobs"), 1)
    count = flat.get(("screenshots", "count"), 10)
    if max_jobs < 1 or count < 1:
        raise ConfigError("max_jobs 和 screenshots.count 必须是正整数")

    aspect = flat.get(("screenshots", "aspect"), ASPECT_MODES[0])
    if aspect not in ASPECT_MODES:
        raise ConfigError(f"screenshots.aspect 只能是 {'、'.join(ASPECT_MODES)}")

    domain = flat.get(("pixhost", "domain"), PIXHOST_DOMAINS[0])
    if domain not in PIXHOST_DOMAINS:
        raise ConfigError(f"pixhost.domain 只能是 {' 或 '.join(PIXHOST_DOMAINS)}")

    piece_length = flat.get(("torrent", "piece_length"), DEFAULT_PIECE_LENGTH)
    if piece_length not in PIECE_LENGTH_RANGE:
        raise ConfigError(f"torrent.piece_length 必须在 {PIECE_LENGTH_RANGE.start}–{PIECE_LENGTH_RANGE.stop - 1} 之间")
    announces = flat.get(("torrent", "announces"), [])
    if not all(isinstance(a, str) and a.strip() for a in announces):
        raise ConfigError("torrent.announces 必须是非空字符串列表")

    template_path = flat.get(("post", "template")) or None
    template_text = flat.get(("post", "template_text")) or None
    try:
        if template_text:
            render_post([PostDisc(name="", mediainfo="", image_urls=[])], template_text)
            template = template_text
        else:
            template = load_template(_expand(template_path) if template_path else None)
    except (OSError, TemplateError) as error:
        raise ConfigError(f"发布说明模板有误：{error}") from None

    max_jobs_limit = 8
    if max_jobs > max_jobs_limit:
        raise ConfigError(f"max_jobs 不能超过 {max_jobs_limit}")

    temp_dir = flat.get(("", "temp_dir"))
    return ServerConfig(
        config_file=file,
        settings_file=settings_file,
        overridden=frozenset(key_name(key) for key in overrides),
        template_file=template_path,
        template_text=template_text,
        database=_expand(flat.get(("", "database"), str(DEFAULT_DATABASE))).absolute(),
        qbit=_qbit_config(flat),
        jackett=_jackett_config(flat),
        roots=tuple(resolved_roots),
        output_dir=_expand(flat.get(("", "output_dir"), str(DEFAULT_OUTPUT_DIR))).absolute(),
        token=token.token,
        token_source=token.source,
        token_file=token.file,
        host=host or flat.get(("", "host"), "127.0.0.1"),
        port=port,
        max_jobs=max_jobs,
        temp_dir=_expand(temp_dir) if temp_dir else None,
        screenshot_count=count,
        aspect=aspect,
        dark_filter=flat.get(("screenshots", "dark_filter"), True),
        pixhost_domain=domain,
        proxy=flat.get(("pixhost", "proxy")) or None,
        announces=tuple(a.strip() for a in announces),
        piece_length=piece_length,
        template=template,
    )
