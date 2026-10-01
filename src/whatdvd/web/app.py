"""FastAPI 应用：认证、目录浏览（限制在 roots 内）、任务、SSE 与结果文件。"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from ..dvd import DVD5_MAX_BYTES, ScanError
from ..post import DEFAULT_TEMPLATE
from ..runner import Runner, SubprocessRunner
from ..sources import find_sources, is_iso
from ..checks import describe_extra_files, find_extra_files
from ..torrent import PIECE_LENGTH_RANGE, make_torrent
from ..upload import Pixhost
from ..workflow import HostFactory, RunOptions, RunResult, check_tools, output_title, run
from .config import ServerConfig
from .jobs import Job, JobManager, JobReporter, stream_events

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


class RunRequest(BaseModel):
    kind: Literal["run"]
    path: str
    count: int = Field(ge=1, le=100)
    upload: bool = True


class TorrentRequest(BaseModel):
    kind: Literal["torrent"]
    path: str
    announces: list[str] = []
    piece_length: int = Field(ge=PIECE_LENGTH_RANGE.start, le=PIECE_LENGTH_RANGE.stop - 1)

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


def create_app(
    config: ServerConfig,
    *,
    runner: Runner | None = None,
    host_factory: HostFactory | None = None,
) -> FastAPI:
    runner = runner or SubprocessRunner()
    host_factory = host_factory or (lambda: Pixhost(config.pixhost_domain, proxy=config.proxy))
    app = FastAPI(title="whatdvd", docs_url=None, redoc_url=None, openapi_url=None)
    manager = JobManager(config.max_jobs)
    app.state.jobs = manager

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    def token_ok(token: str | None) -> bool:
        if not token:
            return False
        return secrets.compare_digest(token.encode(), config.token.encode())

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
            config.token,
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
        if not any(resolved.is_relative_to(root) for root in config.roots):
            raise HTTPException(403, "路径不在允许的目录内")
        return resolved

    def is_allowed(path: Path) -> bool:
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return False
        return any(resolved.is_relative_to(root) for root in config.roots)

    auth = [Depends(require_auth)]

    @app.get("/", include_in_schema=False)
    async def index(request: Request, token: str | None = None) -> Response:
        if token is not None:
            # 启动时打印的链接带 token：写入 cookie 后跳转，去掉地址栏里的 token
            response: Response = RedirectResponse("/", status_code=303)
            if token_ok(token):
                set_cookie(response, request)
            return response
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

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

    @app.get("/api/config", dependencies=auth)
    async def get_config() -> dict[str, Any]:
        return {
            "roots": [str(root) for root in config.roots],
            "screenshot_count": config.screenshot_count,
            "aspect": config.aspect,
            "dark_filter": config.dark_filter,
            "pixhost_domain": config.pixhost_domain,
            "announces": list(config.announces),
            "piece_length": config.piece_length,
            "piece_length_range": [PIECE_LENGTH_RANGE.start, PIECE_LENGTH_RANGE.stop - 1],
            "listen": f"{config.host}:{config.port}",
            "output_dir": str(config.output_dir),
            "temp_dir": str(config.temp_dir) if config.temp_dir else None,
            "max_jobs": config.max_jobs,
            "proxy": bool(config.proxy),
            "custom_template": config.template != DEFAULT_TEMPLATE,
        }

    @app.get("/api/source", dependencies=auth)
    async def source(path: str) -> dict[str, Any]:
        """所选路径下的盘：只看文件大小，不调用 mediainfo，足够快。"""
        target = resolve_allowed(path)
        try:
            sources = await asyncio.to_thread(find_sources, target)
        except ScanError:
            sources = []
        discs = [_disc_summary(item) for item in sources]
        return {
            "path": str(target),
            "name": target.name,
            "kind": _entry_kind(target) or "dir",
            "discs": discs,
            "total_bytes": sum(d["bytes"] for d in discs),
        }

    @app.get("/api/browse", dependencies=auth)
    async def browse(path: str | None = None) -> dict[str, Any]:
        if path is None:
            entries = [
                {"name": str(root), "path": str(root), "kind": _entry_kind(root) or "dir"} for root in config.roots
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
        parent = None if current in config.roots else str(current.parent)
        return {"path": str(current), "parent": parent, "kind": _entry_kind(current), "entries": entries}

    def run_worker(job: Job, reporter: JobReporter) -> dict[str, Any]:
        options = RunOptions(
            output_dir=job.output_dir,
            count=job.params["count"],
            temp_dir=config.temp_dir,
            upload=job.params["upload"],
            template=config.template,
            aspect=config.aspect,
            dark_filter=config.dark_filter,
        )
        return _serialize_run(run(runner, job.path, options, reporter, host_factory))

    def torrent_worker(job: Job, reporter: JobReporter) -> dict[str, Any]:
        check_tools(runner, ["mktorrent"])
        extra = find_extra_files(job.path)
        for line in describe_extra_files(extra):
            reporter.info(line)
        reporter.info(f"开始做种：{job.path.name}（计算哈希，DVD9 可能需要几分钟）")
        started = time.monotonic()
        output = make_torrent(
            runner,
            job.path,
            job.output_dir,
            announces=job.params["announces"],
            piece_length=job.params["piece_length"],
        )
        reporter.info(f"种子：{output.name}（用时 {time.monotonic() - started:.0f} 秒）")
        return {
            "ok": True,
            "torrent_file": output.name,
            "extra_files": [{"path": item.path.as_posix(), "reason": item.reason} for item in extra],
            "files": [output.name],
        }

    @app.post("/api/jobs", dependencies=auth, status_code=201)
    async def create_job(body: JobRequest) -> dict[str, Any]:
        path = resolve_allowed(body.path)
        output_dir = config.output_dir / output_title(path)
        if isinstance(body, RunRequest):
            params: dict[str, Any] = {"count": body.count, "upload": body.upload}
            job = manager.submit(Job("run", path, params, output_dir), run_worker)
        else:
            params = {"announces": body.announces, "piece_length": body.piece_length}
            job = manager.submit(Job("torrent", path, params, output_dir), torrent_worker)
        return job.summary()

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
