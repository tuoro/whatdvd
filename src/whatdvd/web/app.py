"""FastAPI 应用：认证、目录浏览（限制在 roots 内）、任务、SSE 与结果文件。"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import replace
from collections.abc import AsyncIterator, Awaitable, Callable
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from ..dvd import DVD5_MAX_BYTES, ScanError
from ..post import DEFAULT_TEMPLATE
from ..runner import Runner, SubprocessRunner
from ..sources import find_sources, is_iso
from ..checks import describe_extra_files, find_extra_files
from ..imdb_dataset import Cancelled, DatasetError, ImdbDataset, Progress, build
from ..indexer import IndexerError, Jackett
from ..qbit import QBittorrent, QbitError
from ..resolution import ASPECT_MODES
from ..naming import clean_title
from ..release_names import audio_from_mediainfo, bhd_title, disc_kind, guess_query, ptp_name
from ..rutor import Rutor
from ..seedlink import LinkError, check_name, link_tree, same_filesystem, target_name
from ..store import Status, Store
from ..tmdb import Match, Tmdb, TmdbError
from ..torrent import PIECE_LENGTH_RANGE, make_torrent
from ..upload import PIXHOST_DOMAINS, Pixhost
from ..workflow import HostFactory, RunOptions, RunResult, check_tools, output_title, run
from .config import (
    JackettConfig,
    EDITABLE,
    SECRETS,
    ConfigError,
    ServerConfig,
    key_name,
    load_config,
    read_settings,
    write_settings,
)
from .jobs import Job, JobManager, JobReporter, stream_events
from .watcher import Watcher, WatcherError

COOKIE = "whatdvd_token"
COOKIE_MAX_AGE = 30 * 24 * 3600
STATIC_DIR = Path(str(files("whatdvd.web") / "static"))

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class LoginRequest(BaseModel):
    token: str


class QbitTest(BaseModel):
    url: str
    username: str = ""
    password: str | None = None
    """None 表示用已保存的密码。"""


class RutorTest(BaseModel):
    url: str


class TmdbTest(BaseModel):
    api_key: str | None = None
    """None 表示用已保存的 API Key。"""


class JackettTest(BaseModel):
    url: str
    api_key: str | None = None
    """None 表示用已保存的 API Key。"""


def settings_values(c: ServerConfig) -> dict[str, Any]:
    """设置页面中可以修改的项的当前值；密码和 API Key 只返回是否已设置。"""
    qb, jk = c.qbit, c.jackett
    return {
        "max_jobs": c.max_jobs,
        "temp_dir": str(c.temp_dir) if c.temp_dir else "",
        "seed_dir": str(c.seed_dir) if c.seed_dir else "",
        "auto_rename": c.auto_rename,
        "screenshots": {"count": c.screenshot_count, "aspect": c.aspect, "dark_filter": c.dark_filter},
        "pixhost": {"domain": c.pixhost_domain, "proxy": c.proxy or ""},
        "torrent": {"announces": list(c.announces), "piece_length": c.piece_length},
        "post": {"template_text": c.template_text or "", "template": c.template_file or ""},
        "qbittorrent": {
            "url": qb.url if qb else "",
            "username": qb.username if qb else "",
            "password_set": bool(qb and qb.password),
            "category": qb.category if qb else "whatdvd",
            "seed_category": qb.seed_category if qb else "whatdvd-seed",
            "save_path": (qb.save_path or "") if qb else "",
            "path_map": dict(qb.path_map) if qb else {},
            "interval": qb.interval if qb else 60,
        },
        "rutor": {
            "url": c.rutor.url if c.rutor else "",
            "queries": list(c.rutor.queries) if c.rutor else ["DVD9", "DVD5"],
            "interval": c.rutor.interval if c.rutor else 60,
            "films_only": c.rutor.films_only if c.rutor else True,
        },
        "jackett": {
            "url": jk.url if jk else "",
            "api_key_set": bool(jk and jk.api_key),
            "indexer": jk.indexer if jk else "all",
            "queries": list(jk.queries) if jk else list(JackettConfig.queries),
            "interval": jk.interval if jk else 60,
            "films_only": jk.films_only if jk else True,
        },
        "tmdb": {"api_key_set": bool(c.tmdb_api_key)},
    }


class TitleChoice(BaseModel):
    """在来源页从 TMDB 选中的片名。"""

    title: str = Field(min_length=1, max_length=300)
    original_title: str = Field("", max_length=300)
    original_language: str = Field("", max_length=10)
    year: int | None = Field(None, ge=1870, le=2100)
    imdb_id: str | None = Field(None, pattern=r"^tt\d{5,10}$")
    tmdb_url: str | None = Field(None, pattern=r"^https://www\.themoviedb\.org/(movie|tv)/\d+$")


class RunRequest(BaseModel):
    kind: Literal["run"]
    path: str
    count: int = Field(ge=1, le=100)
    upload: bool = True
    seed_name: str = ""
    """发种名称（最外层文件夹或 ISO 的名字），空为原名。需要配置发种目录。"""
    title: TitleChoice | None = None
    region: str = Field("", max_length=60)
    """BHD 标题中的地区或发行商，例如 RUS、Criterion Collection。"""
    edition: str = Field("", max_length=60)


class TorrentRequest(BaseModel):
    kind: Literal["torrent"]
    path: str
    announces: list[str] = []
    piece_length: int = Field(ge=PIECE_LENGTH_RANGE.start, le=PIECE_LENGTH_RANGE.stop - 1)
    seed_name: str = ""

    @field_validator("announces")
    @classmethod
    def _check_announces(cls, value: list[str]) -> list[str]:
        cleaned = [a.strip() for a in value if a.strip()]
        for announce in cleaned:
            if not announce.startswith(("http://", "https://", "udp://")):
                raise ValueError(f"announce 地址必须以 http://、https:// 或 udp:// 开头：{announce}")
        return cleaned


JobRequest = Annotated[RunRequest | TorrentRequest, Field(discriminator="kind")]


def _entry_kind(path: Path) -> str | None:
    """目录浏览中显示的条目类型；None 表示不显示。"""
    try:
        if path.is_dir():
            if path.name.upper() == "VIDEO_TS":
                return "dvd"
            has_video_ts = any(child.name.upper() == "VIDEO_TS" and child.is_dir() for child in path.iterdir())
            return "dvd" if has_video_ts else "dir"
        if path.is_file() and is_iso(path):
            return "iso"
    except OSError:
        return None
    return None


def _disc_summary(source: Path) -> dict[str, Any]:
    if is_iso(source):
        name, size = source.stem, source.stat().st_size
    else:
        name = source.parent.name
        size = sum(p.stat().st_size for p in source.iterdir() if p.is_file())
    return {
        "name": name,
        "path": str(source),
        "kind": "iso" if is_iso(source) else "dvd",
        "bytes": size,
        "media_type": "DVD5" if size <= DVD5_MAX_BYTES else "DVD9",
    }


def _site_names(result: RunResult, params: dict[str, Any]) -> dict[str, Any] | None:
    """选了片名时给出 BHD 标题：制式和盘型来自识别结果，音轨来自 VOB 的 MediaInfo。"""
    chosen = params.get("title")
    analyzed = [d for d in result.discs if d.analysis is not None]
    if not chosen or not analyzed:
        return None
    first = analyzed[0]
    assert first.analysis is not None
    audio = None
    if first.output is not None and first.output.mediainfo.is_file():
        audio = audio_from_mediainfo(first.output.mediainfo.read_text(encoding="utf-8"))
    bhd = bhd_title(
        title=chosen["title"],
        original_title=chosen.get("original_title", ""),
        original_language=chosen.get("original_language", ""),
        year=chosen.get("year"),
        standard=first.analysis.standard,
        kind=disc_kind([d.analysis.disc.media_type for d in analyzed if d.analysis is not None]),
        audio=audio,
        region=params.get("region", ""),
        edition=params.get("edition", ""),
    )
    return {"bhd": bhd, "audio": audio, "imdb_id": chosen.get("imdb_id"), "tmdb_url": chosen.get("tmdb_url")}


def _serialize_run(result: RunResult) -> dict[str, Any]:
    names: list[str] = []
    discs: list[dict[str, Any]] = []
    for disc in result.discs:
        item: dict[str, Any] = {"label": disc.label, "source": str(disc.source), "error": disc.error}
        if disc.analysis is not None:
            analysis = disc.analysis
            item.update(
                name=analysis.disc.name,
                vob=analysis.disc.vob.name,
                ifo=analysis.disc.ifo.name if analysis.disc.ifo else None,
                media_type=analysis.disc.media_type,
                total_bytes=analysis.disc.total_bytes,
                standard=analysis.standard,
                width=analysis.video.width,
                height=analysis.video.height,
                par=analysis.video.par_text,
                size=list(analysis.size),
                duration=analysis.duration,
            )
        if disc.output is not None:
            urls = {upload.path.name: upload.image.direct_url for upload in disc.uploads if upload.image}
            item["screenshots"] = [
                {"file": shot.path.name, "at": shot.at, "ok": shot.ok, "url": urls.get(shot.path.name)}
                for shot in disc.output.shots
            ]
            item["mediainfo_file"] = disc.output.mediainfo.name
            names += [shot.path.name for shot in disc.output.shots if shot.ok]
            names.append(disc.output.mediainfo.name)
        discs.append(item)

    post_file = post_text = None
    if result.post_path is not None:
        post_file = result.post_path.name
        post_text = result.post_path.read_text(encoding="utf-8")
        names.append(post_file)
    return {"ok": result.ok, "discs": discs, "post_file": post_file, "post": post_text, "files": names}


JACKETT_DELAY = 2.0
"""通过 Jackett 连续搜索时的间隔（秒），避免触发 kinozal 等站点的防刷限制。"""

RELEASE_GROUPS: dict[str, tuple[Status, ...]] = {
    "new": ("new",),
    "active": ("sent", "downloading", "processing"),
    "finished": ("done", "failed"),
    "ignored": ("ignored",),
}


def create_app(
    config: ServerConfig,
    *,
    runner: Runner | None = None,
    host_factory: HostFactory | None = None,
    qbit: QBittorrent | None = None,
    jackett: Jackett | None = None,
    rutor: Rutor | None = None,
    tmdb_factory: Callable[[str], Tmdb] = Tmdb,
    imdb_builder: Callable[[Path, Progress, threading.Event], dict[str, Any]] | None = None,
    background: bool = True,
) -> FastAPI:
    """qbit / jackett 不传时按配置创建；background=False 时不启动后台轮询（测试用）。

    设置页面保存后，live 换成新的配置：之后的任务、图床、qB / Jackett 连接都按新配置。
    """
    runner = runner or SubprocessRunner()
    live = [config]

    def cfg() -> ServerConfig:
        return live[0]

    host_factory = host_factory or (lambda: Pixhost(cfg().pixhost_domain, proxy=cfg().proxy))
    manager = JobManager(config.max_jobs)
    watcher: Watcher | None = None
    watcher_task: asyncio.Task[None] | None = None
    store: Store | None = None

    async def start_watcher(
        qb: QBittorrent | None = None, jk: Jackett | None = None, ru: Rutor | None = None
    ) -> None:
        nonlocal watcher, watcher_task, store
        c = cfg()
        if ru is None and c.rutor is not None:
            ru = Rutor(c.rutor.url)
        if qb is None and c.qbit is not None:
            qb = QBittorrent(c.qbit.url, c.qbit.username, c.qbit.password)
        if jk is None and c.jackett is not None:
            jk = Jackett(
                c.jackett.url, c.jackett.api_key, indexer=c.jackett.indexer, films_only=c.jackett.films_only,
                delay=JACKETT_DELAY,
            )
        if qb is None and jk is None and ru is None:
            return
        store = store or Store(c.database)
        watcher = Watcher(c, store, submit_run=submit_auto, get_job=manager.get, qbit=qb, jackett=jk, rutor=ru)
        app.state.watcher = watcher
        if background:
            watcher_task = asyncio.create_task(watcher.run_forever())

    async def stop_watcher() -> None:
        nonlocal watcher, watcher_task
        if watcher_task is not None:
            watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher_task
            watcher_task = None
        if watcher is not None:
            watcher.close()
            watcher = None

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await start_watcher(qbit, jackett, rutor)
        try:
            yield
            imdb_stop.set()  # 停止正在进行的 IMDb 数据集下载
        finally:
            await stop_watcher()
            if store is not None:
                store.close()

    app = FastAPI(title="whatdvd", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.jobs = manager

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path.endswith((".js", ".css")) and request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"  # 每次都向服务器确认，升级后不会用旧的脚本
        return response

    def token_ok(token: str | None) -> bool:
        if not token:
            return False
        return secrets.compare_digest(token.encode(), cfg().token.encode())

    def require_auth(request: Request) -> None:
        token = request.cookies.get(COOKIE)
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            token = header.removeprefix("Bearer ")
        if not token_ok(token):
            raise HTTPException(401, "未登录或登录已过期")

    def set_cookie(response: Response, request: Request) -> None:
        response.set_cookie(
            COOKIE,
            cfg().token,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )

    def resolve_allowed(raw: str) -> Path:
        path = Path(raw)
        if not path.is_absolute():
            raise HTTPException(400, "路径必须是绝对路径")
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise HTTPException(404, "路径不存在") from None
        if not any(resolved.is_relative_to(root) for root in cfg().roots):
            raise HTTPException(403, "路径不在允许的目录内")
        return resolved

    def is_allowed(path: Path) -> bool:
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return False
        return any(resolved.is_relative_to(root) for root in cfg().roots)

    auth = [Depends(require_auth)]

    # 脚本和样式的地址带上内容的哈希：升级后地址变了，浏览器不会继续用缓存里的旧版本
    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for asset in ("app.js", "style.css"):
        digest = hashlib.sha256((STATIC_DIR / asset).read_bytes()).hexdigest()[:12]
        index_html = index_html.replace(f'"/static/{asset}"', f'"/static/{asset}?v={digest}"')

    @app.get("/", include_in_schema=False)
    async def index(request: Request, token: str | None = None) -> Response:
        if token is not None:
            # 启动时打印的链接带 token：写入 cookie 后跳转，去掉地址栏里的 token
            response: Response = RedirectResponse("/", status_code=303)
            if token_ok(token):
                set_cookie(response, request)
            return response
        return Response(index_html, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.post("/api/login", status_code=204)
    async def login(body: LoginRequest, request: Request) -> Response:
        if not token_ok(body.token.strip()):
            await asyncio.sleep(0.5)  # 减慢猜测
            raise HTTPException(401, "token 不正确")
        response = Response(status_code=204)
        set_cookie(response, request)
        return response

    @app.post("/api/logout", status_code=204)
    async def logout() -> Response:
        response = Response(status_code=204)
        response.delete_cookie(COOKIE)
        return response

    def seed_dir_status(c: ServerConfig) -> dict[str, Any] | None:
        """发种目录，以及哪些浏览目录和它不在同一个文件系统（这些目录里的盘无法建立硬链接）。"""
        if c.seed_dir is None:
            return None
        try:
            c.seed_dir.mkdir(parents=True, exist_ok=True)
            other = [str(root) for root in c.roots if not same_filesystem(root, c.seed_dir)]
            error = None
        except OSError as exc:
            other, error = [], f"无法创建发种目录：{exc}"
        return {"path": str(c.seed_dir), "other_filesystem": other, "error": error}

    @app.get("/api/config", dependencies=auth)
    async def get_config() -> dict[str, Any]:
        c = cfg()
        return {
            "seed_dir": seed_dir_status(c),
            "roots": [str(root) for root in c.roots],
            "screenshot_count": c.screenshot_count,
            "aspect": c.aspect,
            "dark_filter": c.dark_filter,
            "pixhost_domain": c.pixhost_domain,
            "announces": list(c.announces),
            "piece_length": c.piece_length,
            "piece_length_range": [PIECE_LENGTH_RANGE.start, PIECE_LENGTH_RANGE.stop - 1],
            "listen": f"{c.host}:{c.port}",
            "output_dir": str(c.output_dir),
            "temp_dir": str(c.temp_dir) if c.temp_dir else None,
            "max_jobs": c.max_jobs,
            "proxy": bool(c.proxy),
            "custom_template": c.template != DEFAULT_TEMPLATE,
            "qbit": {"url": c.qbit.url, "category": c.qbit.category, "seed_category": c.qbit.seed_category,
                     "path_map": c.qbit.path_map}
            if c.qbit
            else None,
            "rutor": {"url": c.rutor.url, "queries": c.rutor.queries, "interval": c.rutor.interval} if c.rutor else None,
            "jackett": {"url": c.jackett.url, "indexer": c.jackett.indexer, "queries": c.jackett.queries,
                        "interval": c.jackett.interval}
            if c.jackett
            else None,
        }

    # ---------- 设置 ----------

    def settings_payload() -> dict[str, Any]:
        c = cfg()
        return {
            "values": settings_values(c),
            "default_template": DEFAULT_TEMPLATE,
            "overridden": sorted(c.overridden),
            "fixed": {
                "listen": f"{c.host}:{c.port}",
                "roots": [str(root) for root in c.roots],
                "output_dir": str(c.output_dir),
                "database": str(c.database),
                "token": {"config": "配置文件", "env": "环境变量 WHATDVD_TOKEN", "file": str(c.token_file),
                          "new": str(c.token_file)}.get(c.token_source, c.token_source),
                "config_file": str(c.config_file) if c.config_file else None,
                "settings_file": str(c.settings_file),
            },
            "options": {
                "aspect_modes": list(ASPECT_MODES),
                "pixhost_domains": list(PIXHOST_DOMAINS),
                "piece_length_range": [PIECE_LENGTH_RANGE.start, PIECE_LENGTH_RANGE.stop - 1],
            },
        }

    async def apply_settings(overrides: dict[tuple[str, str], Any]) -> None:
        """校验通过才写入设置文件，然后换成新配置：调整任务并发数，必要时重建 qB / Jackett 连接。"""
        old = cfg()
        try:
            new = load_config(old.config_file, host=old.host, port=old.port, roots=old.roots, settings=overrides)
        except ConfigError as error:
            raise HTTPException(400, str(error)) from None
        new = replace(new, token=old.token, token_source=old.token_source, token_file=old.token_file)
        try:
            write_settings(old.settings_file, overrides)
        except OSError as error:
            raise HTTPException(500, f"无法保存设置到 {old.settings_file}：{error.strerror}") from None
        live[0] = new
        if new.max_jobs != old.max_jobs:
            await manager.set_limit(new.max_jobs)
        if (new.qbit, new.jackett, new.rutor) != (old.qbit, old.jackett, old.rutor):
            await stop_watcher()
            await start_watcher()

    @app.get("/api/settings", dependencies=auth)
    async def get_settings() -> dict[str, Any]:
        return settings_payload()

    @app.put("/api/settings", dependencies=auth)
    async def put_settings(body: dict[str, Any]) -> dict[str, Any]:
        """只需提交改动的项。值为 null：去掉设置页面的覆盖，恢复为配置文件或默认值；
        密码和 API Key 为 null 时保持不变，为 "" 时清空。"""
        try:
            overrides = read_settings(cfg().settings_file)
        except ConfigError as error:
            raise HTTPException(500, str(error)) from None
        tables = {table for table, _ in EDITABLE if table}
        changes: list[tuple[tuple[str, str], Any]] = []
        for name, value in body.items():
            if name in tables:
                if not isinstance(value, dict):
                    raise HTTPException(400, f"{name} 应该是一个表")
                changes += [((name, key), item) for key, item in value.items()]
            else:
                changes.append((("", name), value))
        for key, value in changes:
            if key not in EDITABLE:
                raise HTTPException(400, f"{key_name(key)} 不能在设置页面中修改")
            if value is None:
                if key not in SECRETS:
                    overrides.pop(key, None)
            else:
                overrides[key] = value
        await apply_settings(overrides)
        return settings_payload()

    @app.delete("/api/settings", dependencies=auth)
    async def reset_settings() -> dict[str, Any]:
        """去掉设置页面保存的所有设置，恢复为配置文件和默认值。"""
        await apply_settings({})
        return settings_payload()

    @app.post("/api/settings/test/qbittorrent", dependencies=auth)
    async def test_qbittorrent(body: QbitTest) -> dict[str, Any]:
        c = cfg()
        password = body.password if body.password is not None else (c.qbit.password if c.qbit else "")
        client = QBittorrent(body.url.strip(), body.username, password, timeout=10)
        try:
            version = await asyncio.to_thread(client.version)
        except QbitError as error:
            raise HTTPException(400, str(error)) from None
        finally:
            client.close()
        return {"version": version}

    @app.post("/api/settings/test/rutor", dependencies=auth)
    async def test_rutor(body: RutorTest) -> dict[str, Any]:
        url = body.url.strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "地址必须以 http:// 或 https:// 开头")
        client = Rutor(url, delay=0, timeout=20)
        try:
            total, releases = await asyncio.to_thread(client.search, "DVD9")
        except IndexerError as error:
            raise HTTPException(400, str(error)) from None
        finally:
            client.close()
        return {"total": total, "page": len(releases)}

    @app.post("/api/settings/test/tmdb", dependencies=auth)
    async def test_tmdb(body: TmdbTest) -> dict[str, Any]:
        api_key = (body.api_key if body.api_key is not None else cfg().tmdb_api_key).strip()
        if not api_key:
            raise HTTPException(400, "请填写 TMDB API Key")
        client = tmdb_factory(api_key)
        try:
            await asyncio.to_thread(client.check)
        except TmdbError as error:
            raise HTTPException(400, str(error)) from None
        finally:
            client.close()
        return {"ok": True}

    @app.post("/api/settings/test/jackett", dependencies=auth)
    async def test_jackett(body: JackettTest) -> dict[str, Any]:
        c = cfg()
        api_key = body.api_key if body.api_key is not None else (c.jackett.api_key if c.jackett else "")
        client = Jackett(body.url.strip(), api_key, timeout=30)
        try:
            indexers = await asyncio.to_thread(client.indexers)
        except IndexerError as error:
            raise HTTPException(400, str(error)) from None
        finally:
            client.close()
        return {"indexers": [{"id": i, "name": n} for i, n in indexers]}

    # ---------- 资源候选（Jackett + qBittorrent） ----------

    def get_watcher() -> Watcher:
        if watcher is None:
            raise HTTPException(404, "没有配置 Jackett、rutor 直连或 qBittorrent")
        return watcher

    def watcher_error(error: WatcherError) -> HTTPException:
        return HTTPException(409, str(error))

    @app.get("/api/releases", dependencies=auth)
    async def list_releases(
        group: Literal["new", "active", "finished", "ignored"] = "new",
        q: str = "",
        kind: Literal["", "DVD9", "DVD5", "multi"] = "",
        seeded: bool = False,
        clean: bool = False,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        """q：标题中包含的文字（不区分大小写）；kind：DVD9 / DVD5 单盘或 multi 多盘；
        seeded：只看有做种者的；clean：只看没有提示的。"""
        current = get_watcher()
        counts = {name: len(current.store.list(statuses)) for name, statuses in RELEASE_GROUPS.items()}
        records = current.store.list(RELEASE_GROUPS[group])
        words = q.casefold().split()
        if words:
            records = [r for r in records if all(word in r.title.casefold() for word in words)]
        if kind == "multi":
            records = [r for r in records if r.discs > 1]
        elif kind:
            records = [r for r in records if r.kind == kind]
        if seeded:
            records = [r for r in records if r.seeders]
        if clean:
            records = [r for r in records if not r.warnings]
        return {
            "releases": [r.public() for r in records[offset : offset + limit]],
            "total": len(records),
            "counts": counts,
            "status": current.status(),
        }

    @app.post("/api/releases/refresh", dependencies=auth)
    async def refresh_releases() -> dict[str, Any]:
        current = get_watcher()
        try:
            added = await current.search()
        except WatcherError as error:
            raise watcher_error(error) from None
        return {"added": added}

    @app.post("/api/releases/backfill", dependencies=auth, status_code=202)
    async def backfill_releases() -> dict[str, Any]:
        """按“关键词 年份”全面搜索，在后台进行；进度见 /api/releases 的 status.backfill。"""
        try:
            total = get_watcher().start_backfill()
        except WatcherError as error:
            raise watcher_error(error) from None
        return {"total": total}

    @app.post("/api/releases/sync", dependencies=auth, status_code=204)
    async def sync_releases() -> None:
        try:
            await get_watcher().sync()
        except WatcherError as error:
            raise watcher_error(error) from None

    def release_or_404(release_id: str) -> Any:
        record = get_watcher().store.get(release_id)
        if record is None:
            raise HTTPException(404, "找不到这个资源")
        return record

    @app.post("/api/releases/{release_id}/download", dependencies=auth)
    async def download_release(release_id: str) -> dict[str, Any]:
        release_or_404(release_id)
        try:
            record = await get_watcher().download(release_id)
        except WatcherError as error:
            raise watcher_error(error) from None
        return record.public()

    @app.post("/api/releases/{release_id}/reprocess", dependencies=auth)
    async def reprocess_release(release_id: str) -> dict[str, Any]:
        release_or_404(release_id)
        try:
            record = await get_watcher().reprocess(release_id)
        except WatcherError as error:
            raise watcher_error(error) from None
        return record.public()

    @app.post("/api/releases/{release_id}/ignore", dependencies=auth)
    async def ignore_release(release_id: str, undo: bool = False) -> dict[str, Any]:
        record = release_or_404(release_id)
        if record.status not in (("ignored",) if undo else ("new",)):
            raise HTTPException(409, "只有候选可以忽略，只有已忽略的可以恢复")
        return get_watcher().store.update(release_id, status="new" if undo else "ignored").public()

    @app.get("/api/source", dependencies=auth)
    async def source(path: str) -> dict[str, Any]:
        """所选路径下的盘：只看文件大小，不调用 mediainfo，足够快。"""
        target = resolve_allowed(path)
        try:
            sources = await asyncio.to_thread(find_sources, target)
        except ScanError:
            sources = []
        discs = [_disc_summary(item) for item in sources]
        # 从资源页下载来的，用种子标题猜片名（常带英文名和年份）；否则用文件夹名
        record = watcher.store.by_local_path(str(target)) if watcher is not None else None
        query, year = guess_query(record.title if record else (target.stem if target.is_file() else target.name))
        return {
            "path": str(target),
            "name": target.name,
            "kind": _entry_kind(target) or "dir",
            "discs": discs,
            "total_bytes": sum(d["bytes"] for d in discs),
            "disc_kind": disc_kind([d["media_type"] for d in discs]),
            "guess": {"query": query, "year": year, "from": "release" if record else "name"},
            # 自动按 IMDb 改名打开时，有把握的片名预先选中
            "suggested": (await asyncio.to_thread(auto_title, target, record.title if record else ""))[0],
            "tmdb": bool(cfg().tmdb_api_key),
            "imdb_dataset": imdb_dataset().path.is_file(),
        }

    def tmdb_client() -> Tmdb:
        key = cfg().tmdb_api_key
        if not key:
            raise HTTPException(400, "请先在设置页面填写 TMDB API Key")
        return tmdb_factory(key)

    @app.get("/api/tmdb/search", dependencies=auth)
    async def tmdb_search(q: str, year: int | None = None) -> dict[str, Any]:
        """按片名搜 TMDB；也可以直接给 IMDb 链接或编号（TMDB 中没有的，用 IMDb 数据集）。"""
        q = q.strip()
        if not q:
            raise HTTPException(400, "请填写片名，或粘贴 IMDb 链接")
        dataset = imdb_dataset()
        imdb_match = re.search(r"\b(tt\d{5,10})\b", q)
        if imdb_match:
            imdb_id = imdb_match.group(1)
            matches: list[Match] = []
            if cfg().tmdb_api_key:
                client = tmdb_factory(cfg().tmdb_api_key)
                try:
                    matches = await asyncio.to_thread(client.find_imdb, imdb_id)
                except TmdbError:
                    matches = []  # TMDB 查不了时仍可以用 IMDb 数据集
                finally:
                    client.close()
            ids: list[str | None] = [imdb_id] * len(matches)
        elif not cfg().tmdb_api_key:
            if not dataset.path.is_file():
                raise HTTPException(400, "请先在设置页面填写 TMDB API Key 或下载 IMDb 数据集")
            matches, ids = [], []  # 没有 TMDB：只按片名查 IMDb 数据集（在下面）
        else:
            client = tmdb_client()
            try:
                matches = await asyncio.to_thread(client.search, q, year)
                if not matches and year:  # 年份对不上时（例如按发行年份标的）不限年份再搜一次
                    matches = await asyncio.to_thread(client.search, q, None)
                # 搜索结果里没有 IMDb 编号：逐个取详情（并行），好在列表里直接标出 IMDb 的名字
                detailed = await asyncio.gather(
                    *(asyncio.to_thread(client.details, m.kind, m.id) for m in matches), return_exceptions=True
                )
            except TmdbError as error:
                raise HTTPException(502, str(error)) from None
            finally:
                client.close()
            ids = [d.imdb_id if isinstance(d, Match) else None for d in detailed]
        results = []
        for match, imdb_id in zip(matches, ids, strict=True):
            found = await asyncio.to_thread(dataset.title, imdb_id, match.original_language) if imdb_id else None
            imdb = {"id": imdb_id, "title": found.title, "year": found.year} if found else {"id": imdb_id}
            results.append({**match.public(), "imdb_id": imdb_id, "imdb": imdb})
        # IMDb 数据集中按编号或片名找到、而 TMDB 结果里没有的，也列出来（TMDB 漏掉的片、没有 TMDB API Key 时）
        if imdb_match:
            hit = await asyncio.to_thread(dataset.title, imdb_match.group(1))
            extra = [hit] if hit is not None and not results else []
        else:
            extra = await asyncio.to_thread(dataset.find, q, year)
        known = {r["imdb_id"] for r in results}
        for hit in extra:
            if hit.imdb_id not in known:
                results.append({
                    "kind": "imdb", "id": None, "imdb_kind": hit.kind, "title": hit.title,
                    "original_title": hit.original_title, "year": hit.year, "imdb_id": hit.imdb_id, "url": None,
                    "imdb": {"id": hit.imdb_id, "title": hit.title, "year": hit.year},
                })
        info = await asyncio.to_thread(dataset.info)
        notes = []
        if info is not None and not info["searchable"]:
            notes.append("IMDb 数据集是旧版本导入的，不能按片名查找。请在设置页面点“更新数据集”。")
        return {"results": results, "dataset": info is not None, "notes": notes}

    async def site_names(match: Match | None, imdb_id: str | None, disc: str) -> dict[str, Any]:
        """片名以 IMDb 数据集为准（PTP 要求和 IMDb 一致），没有时用 TMDB 的；给出 PTP 发种名称和 BHD 标题开头。"""
        # TMDB 的原始语言只在 IMDb 编号确实属于这个 TMDB 条目时可用（用户可能指定了别的编号）
        language = match.original_language if match and match.imdb_id in (None, imdb_id) else ""
        found = await asyncio.to_thread(imdb_dataset().title, imdb_id, language) if imdb_id else None
        notes: list[str] = []
        if found is not None:
            title, original, year, source = found.title, found.original_title, found.year, "IMDb"
            if match is not None:
                year = year or match.year
                if found.title.casefold() != match.title.casefold():
                    notes.append(f"TMDB 的英文名是“{match.title}”，这里以 IMDb 为准。")
        elif match is None:
            raise HTTPException(404, f"IMDb 数据集中没有 {imdb_id}")
        else:
            title, original, year, source = match.title, match.original_title, match.year, "TMDB"
            if not imdb_id:
                notes.append("TMDB 中没有这部片的 IMDb 编号，片名来自 TMDB。可以把 IMDb 链接粘贴到上面的搜索框再查一次。")
            elif imdb_dataset().info() is None:
                notes.append("还没有下载 IMDb 数据集，片名来自 TMDB，可能和 IMDb 不同。可以在设置页面下载。")
            else:
                notes.append(f"IMDb 数据集中没有 {imdb_id}（可能是新片），片名来自 TMDB。可以在设置页面更新数据集。")
        # BHD 标题的开头（片名、AKA、年份）；地区、制式、音轨在界面和截图任务中补上
        head = bhd_title(
            title=title, original_title=original, original_language=language, year=year, standard=None, kind="",
            audio=None,
        ).removesuffix(" MPEG-2")
        base = match.public() if match else {
            "kind": "imdb", "id": None, "url": None, "original_language": "",
            "imdb_url": f"https://www.imdb.com/title/{imdb_id}/",
        }
        return {
            **base, "imdb_id": imdb_id, "title": title, "original_title": original, "year": year, "source": source,
            "tmdb_title": match.title if match else None, "notes": notes, "ptp_name": ptp_name(title, year, disc),
            "bhd_head": head,
        }

    @app.get("/api/imdb/title/{imdb_id}", dependencies=auth)
    async def imdb_title(imdb_id: Annotated[str, PathParam(pattern=r"^tt\d{5,10}$")], disc: str = "") -> dict[str, Any]:
        """TMDB 中没有的片：只用 IMDb 数据集。"""
        return await site_names(None, imdb_id, disc)

    @app.get("/api/tmdb/{kind}/{tmdb_id}", dependencies=auth)
    async def tmdb_details(
        kind: Literal["movie", "tv"], tmdb_id: int, disc: str = "",
        imdb: Annotated[str | None, Query(pattern=r"^tt\d{5,10}$")] = None,
    ) -> dict[str, Any]:
        """详情和按站点规则给出的名字。disc 为盘型（例如 DVD9、2xDVD9）；imdb 为用户给的 IMDb 编号（TMDB 中没有时）。"""
        client = tmdb_client()
        try:
            match = await asyncio.to_thread(client.details, kind, tmdb_id)
        except TmdbError as error:
            raise HTTPException(502, str(error)) from None
        finally:
            client.close()
        return await site_names(match, imdb if imdb else match.imdb_id, disc)

    # ---------- IMDb 数据集 ----------

    imdb_stop = threading.Event()
    imdb_update: dict[str, Any] = {"running": False, "phase": "", "done": 0, "total": 0, "error": None}

    def imdb_dataset() -> ImdbDataset:
        return ImdbDataset(cfg().database.with_name("imdb.db"))

    @app.get("/api/imdb", dependencies=auth)
    async def imdb_status() -> dict[str, Any]:
        dataset = imdb_dataset()
        return {"path": str(dataset.path), "info": await asyncio.to_thread(dataset.info), "update": dict(imdb_update)}

    @app.post("/api/imdb/update", dependencies=auth, status_code=202)
    async def update_imdb() -> dict[str, Any]:
        """在后台下载并导入，界面轮询 /api/imdb 显示进度。"""
        if imdb_update["running"]:
            raise HTTPException(409, "IMDb 数据集正在更新")
        path = imdb_dataset().path

        def progress(phase: str, done: int, total: int) -> None:
            imdb_update.update(phase=phase, done=done, total=total)

        def work() -> None:
            builder = imdb_builder or (lambda p, report, stop: build(p, report, stop=stop))
            try:
                builder(path, progress, imdb_stop)
                imdb_update["error"] = None
            except Cancelled:
                imdb_update["error"] = "已取消"
            except (DatasetError, OSError, sqlite3.Error) as error:
                imdb_update["error"] = str(error)
            except httpx.HTTPError as error:
                imdb_update["error"] = f"下载失败：{error or type(error).__name__}"
            finally:
                imdb_update.update(running=False, phase="", finished_at=time.time())

        imdb_update.update(running=True, phase="准备下载", done=0, total=0, error=None)
        task = asyncio.get_running_loop().run_in_executor(None, work)
        imdb_tasks.add(task)
        task.add_done_callback(imdb_tasks.discard)
        return dict(imdb_update)

    imdb_tasks: set[asyncio.Future[None]] = set()

    @app.get("/api/browse", dependencies=auth)
    async def browse(path: str | None = None) -> dict[str, Any]:
        if path is None:
            entries = [
                {"name": str(root), "path": str(root), "kind": _entry_kind(root) or "dir"} for root in cfg().roots
            ]
            return {"path": None, "parent": None, "kind": None, "entries": entries}

        current = resolve_allowed(path)
        if not current.is_dir():
            raise HTTPException(400, "不是目录")
        entries = []
        try:
            children = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError as error:
            raise HTTPException(403, f"无法读取目录：{error.strerror}") from None
        for child in children:
            if child.name.startswith(".") or not is_allowed(child):
                continue
            kind = _entry_kind(child)
            if kind is not None:
                entries.append({"name": child.name, "path": str(child), "kind": kind})
        entries.sort(key=lambda e: e["kind"] == "iso")
        parent = None if current in cfg().roots else str(current.parent)
        return {"path": str(current), "parent": parent, "kind": _entry_kind(current), "entries": entries}

    def seed_source(job: Job, reporter: JobReporter) -> Path:
        """配置了发种目录时，先用硬链接把盘放到发种目录（可以改名），之后都处理这一份。"""
        if note := job.params.get("auto_note"):
            reporter.info(note)
        seed_dir = cfg().seed_dir
        if seed_dir is None:
            return job.path
        name = job.params.get("seed_name") or None
        try:
            target, created = link_tree(job.path, seed_dir, name)
        except LinkError as error:
            if not (job.params.get("auto_title") and "不是同一份数据" in str(error)):
                raise
            # 自动选的名字和发种目录中别的盘重名（例如同一部片的另一个版本）：保留原名
            reporter.error(f"{error} 改用原名。")
            target, created = link_tree(job.path, seed_dir, None)
        reporter.info(f"{'已用硬链接放到' if created else '使用发种目录中已有的'} {target}")
        return target

    def run_worker(job: Job, reporter: JobReporter) -> dict[str, Any]:
        path = seed_source(job, reporter)
        options = RunOptions(
            output_dir=job.output_dir,
            count=job.params["count"],
            temp_dir=cfg().temp_dir,
            upload=job.params["upload"],
            template=cfg().template,
            aspect=cfg().aspect,
            dark_filter=cfg().dark_filter,
        )
        result = run(runner, path, options, reporter, host_factory)
        names = _site_names(result, job.params)
        if names:
            reporter.info(f"BHD 标题：{names['bhd']}")
        return {**_serialize_run(result), "seed_path": str(path), "names": names}

    def torrent_worker(job: Job, reporter: JobReporter) -> dict[str, Any]:
        check_tools(runner, ["mktorrent"])
        path = seed_source(job, reporter)
        extra = find_extra_files(path)
        for line in describe_extra_files(extra):
            reporter.info(line)
        reporter.info(f"开始做种：{path.name}（计算哈希，DVD9 可能需要几分钟）")
        started = time.monotonic()
        output = make_torrent(
            runner,
            path,
            job.output_dir,
            announces=job.params["announces"],
            piece_length=job.params["piece_length"],
        )
        reporter.info(f"种子：{output.name}（用时 {time.monotonic() - started:.0f} 秒）")
        return {
            "ok": True,
            "torrent_file": output.name,
            "seed_path": str(path),
            "extra_files": [{"path": item.path.as_posix(), "reason": item.reason} for item in extra],
            "files": [output.name],
        }

    def disc_kind_of(path: Path) -> str:
        try:
            return disc_kind([_disc_summary(source)["media_type"] for source in find_sources(path)])
        except (ScanError, OSError):
            return ""

    def auto_title(path: Path, hint: str) -> tuple[dict[str, Any] | None, str]:
        """自动选片名（设置中“自动按 IMDb 改名”打开时）：返回（片名, 说明）。先用种子标题，再用文件夹名。"""
        if not cfg().auto_rename:
            return None, ""
        dataset = imdb_dataset()
        if not dataset.path.is_file():
            return None, "没有 IMDb 数据集，不自动选片名。可以在设置页面下载。"
        reason = ""
        for text in dict.fromkeys(t for t in (hint, path.stem if path.is_file() else path.name) if t):
            hit, reason = dataset.confident(text)
            if hit is not None:
                title = {"title": hit.title, "original_title": hit.original_title, "original_language": "",
                         "year": hit.year, "imdb_id": hit.imdb_id, "tmdb_url": None}
                return title, f"自动选中片名：{hit.title}（{hit.year or '年份不详'}，{hit.imdb_id}），{reason}。"
        return None, f"没有自动选片名：{reason}。保留原名，可以在来源页选好片名后重新处理。"

    def submit_auto(path: Path, hint: str = "") -> Job:
        """下载完成后的自动处理：有把握时按 IMDb 名改名，并在结果中给出 BHD 标题。"""
        title, note = auto_title(path, hint)
        extra: dict[str, Any] = {"auto_note": note} if note else {}
        seed_name = ""
        if title is not None:
            extra |= {"title": title, "region": "", "edition": "", "auto_title": title["imdb_id"]}
            if cfg().seed_dir is not None:
                seed_name = ptp_name(title["title"], title["year"], disc_kind_of(path))
        return submit_run(path, None, True, seed_name, extra)

    app.state.submit_auto = submit_auto

    def job_output_dir(path: Path, seed_name: str) -> Path:
        """输出目录按发种名称命名（没有时按原名）。"""
        if not seed_name:
            return cfg().output_dir / output_title(path)
        name = target_name(path, seed_name)
        return cfg().output_dir / clean_title(Path(name).stem if path.is_file() else name)

    def submit_run(
        path: Path, count: int | None = None, upload: bool = True, seed_name: str = "", extra: dict[str, Any] | None = None
    ) -> Job:
        params: dict[str, Any] = {
            "count": count or cfg().screenshot_count, "upload": upload, "seed_name": seed_name, **(extra or {}),
        }
        return manager.submit(Job("run", path, params, job_output_dir(path, seed_name)), run_worker)

    @app.post("/api/jobs", dependencies=auth, status_code=201)
    async def create_job(body: JobRequest) -> dict[str, Any]:
        path = resolve_allowed(body.path)
        seed_name = body.seed_name.strip()
        if seed_name:
            if cfg().seed_dir is None:
                raise HTTPException(400, "改发种名称需要先在设置页面填写发种目录")
            try:
                check_name(seed_name)
            except LinkError as error:
                raise HTTPException(400, str(error)) from None
        if isinstance(body, RunRequest):
            extra: dict[str, Any] = {}
            if body.title is not None:
                extra = {"title": body.title.model_dump(), "region": body.region.strip(), "edition": body.edition.strip()}
            job = submit_run(path, body.count, body.upload, seed_name, extra)
        else:
            params: dict[str, Any] = {
                "announces": body.announces, "piece_length": body.piece_length, "seed_name": seed_name,
            }
            job = manager.submit(Job("torrent", path, params, job_output_dir(path, seed_name)), torrent_worker)
        return job.summary()

    @app.post("/api/jobs/{job_id}/seed", dependencies=auth)
    async def seed_job(job_id: str) -> dict[str, Any]:
        """把做好的种子添加到 qB 做种（数据在发种目录中，跳过校验）。"""
        job = get_job(job_id)
        result = job.result or {}
        if job.kind != "torrent" or job.status != "done" or not result.get("torrent_file"):
            raise HTTPException(409, "只有完成的做种任务可以添加到 qBittorrent")
        torrent_file = job.output_dir / result["torrent_file"]
        data = Path(result["seed_path"])
        if not torrent_file.is_file() or not data.exists():
            raise HTTPException(409, "种子文件或数据已被删除")
        try:
            added, save_path = await get_watcher().seed(torrent_file.read_bytes(), data)
        except WatcherError as error:
            raise watcher_error(error) from None
        result["seeded"] = True
        qb = cfg().qbit
        return {"added": added, "save_path": save_path, "category": qb.seed_category if qb else None}

    @app.get("/api/jobs", dependencies=auth)
    async def list_jobs() -> list[dict[str, Any]]:
        return [job.summary() for job in manager.list()]

    def get_job(job_id: str) -> Job:
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(404, "任务不存在")
        return job

    @app.get("/api/jobs/{job_id}", dependencies=auth)
    async def job_detail(job_id: str) -> dict[str, Any]:
        return get_job(job_id).detail()

    @app.get("/api/jobs/{job_id}/events", dependencies=auth)
    async def job_events(job_id: str, request: Request, after: Annotated[int, Query(ge=0)] = 0) -> StreamingResponse:
        job = get_job(job_id)
        last_id = request.headers.get("Last-Event-ID", "")
        start = int(last_id) if last_id.isdigit() else after
        return StreamingResponse(
            stream_events(job, start),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/jobs/{job_id}/files/{name}", dependencies=auth)
    async def job_file(job_id: str, name: str) -> FileResponse:
        job = get_job(job_id)
        if name not in job.files:
            raise HTTPException(404, "文件不存在")
        path = job.output_dir / name
        if not path.is_file():
            raise HTTPException(404, "文件已被删除")
        if name.endswith(".torrent"):
            return FileResponse(path, media_type="application/x-bittorrent", filename=name)
        if name.endswith(".txt"):
            return FileResponse(path, media_type="text/plain; charset=utf-8")
        return FileResponse(path)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
