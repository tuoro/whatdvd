"""后台轮询：定时用 Jackett 搜索 DVD 原盘写入候选；跟踪 qBittorrent 中的下载，完成后自动处理。

候选只有用户点了“下载”才会推送到 qB。在 qB 中手动添加到同一分类的种子，下载完成后也会自动处理。
"""

from __future__ import annotations

import asyncio
import datetime
import functools
import hashlib
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..indexer import IndexerError, Jackett, Release, Verdict, classify, inspect_contents
from ..qbit import PathMap, QBittorrent, QbitError, magnet_info_hash, torrent_contents, torrent_info_hash
from ..rutor import SOURCE as RUTOR_SOURCE
from ..rutor import Rutor
from ..store import Record, Status, Store
from .config import ServerConfig
from .jobs import Job

log = logging.getLogger("whatdvd.watcher")

ACTIVE: tuple[Status, ...] = ("sent", "downloading")

BACKFILL_FROM = 1920
"""全面搜索从这一年开始。站点每次搜索最多返回 100 条，按“关键词 年份”拆开才能搜到更早的资源。"""


Step = tuple[str, Callable[[], Sequence[Release]]]
"""一次搜索：（说明，执行后返回资源）。"""

MAX_CONSECUTIVE_FAILURES = 3
"""连续失败这么多次就停止（网站或 Jackett 不可用时，不再发出剩下的请求）。"""


def _relative_files(path: Path, limit: int = 5000) -> list[str]:
    if path.is_file():
        return [path.name]
    files = []
    for item in path.rglob("*"):
        if item.is_file():
            files.append(item.relative_to(path).as_posix())
            if len(files) >= limit:
                break
    return files


def _first_page(rutor: Rutor, query: str) -> list[Release]:
    return rutor.search(query)[1]


def _rutor_step(rutor: Rutor, query: str, films_only: bool, all_pages: bool) -> Callable[[], list[Release]]:
    if films_only:
        return functools.partial(rutor.films, query, all_pages)
    return functools.partial(rutor.search_all, query) if all_pages else functools.partial(_first_page, rutor, query)


def backfill_queries(queries: Sequence[str], until: int | None = None) -> list[str]:
    """关键词 × 年份，近的年份在前。"""
    until = until or datetime.date.today().year
    return [f"{query} {year}" for year in range(until, BACKFILL_FROM - 1, -1) for query in queries]


class WatcherError(RuntimeError):
    pass


@dataclass
class _State:
    last_search: float | None = None
    search_error: str | None = None
    last_added: int = 0
    last_sync: float | None = None
    sync_error: str | None = None
    backfill_running: bool = False
    backfill_done: int = 0
    backfill_total: int = 0
    backfill_added: int = 0
    last_backfill: float | None = None
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    """来源（Jackett、rutor）→ 上次搜索的情况：时间、结果数、最新一条的发布时间、错误。"""
    schedule: dict[str, dict[str, Any]] = field(default_factory=dict)
    """来源 → 自动搜索的间隔（分钟，0 为只手动搜索）和下次时间。"""


class Watcher:
    def __init__(
        self,
        config: ServerConfig,
        store: Store,
        *,
        submit_run: Callable[[Path, str], Job],  # （下载完成的路径, 种子标题）→ 处理任务；种子标题用来自动选片名
        get_job: Callable[[str], Job | None],
        qbit: QBittorrent | None = None,
        jackett: Jackett | None = None,
        rutor: Rutor | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.qbit = qbit
        self.jackett = jackett
        self.rutor = rutor
        self._submit_run = submit_run
        self._get_job = get_job
        self._path_map = PathMap(config.qbit.path_map if config.qbit else ())
        self._search_lock = asyncio.Lock()
        self._backfill_task: asyncio.Task[None] | None = None
        self._sync_lock = asyncio.Lock()
        self.state = _State()

    def status(self) -> dict[str, Any]:
        return {
            "jackett": self.jackett is not None,
            "rutor": self.rutor is not None,
            "qbit": self.qbit is not None,
            "last_search": self.state.last_search,
            "search_error": self.state.search_error,
            "last_added": self.state.last_added,
            "searching": self._search_lock.locked(),
            "last_sync": self.state.last_sync,
            "sync_error": self.state.sync_error,
            "sources": self.state.sources,
            "schedule": self.state.schedule,
            "backfill": {
                "running": self.state.backfill_running,
                "done": self.state.backfill_done,
                "total": self.state.backfill_total,
                "added": self.state.backfill_added,
                "last": self.state.last_backfill,
            },
        }

    def close(self) -> None:
        if self._backfill_task is not None:
            self._backfill_task.cancel()
        if self.qbit is not None:
            self.qbit.close()
        if self.jackett is not None:
            self.jackett.close()
        if self.rutor is not None:
            self.rutor.close()

    # ---------- 搜索 ----------

    def _ingest(self, releases: Sequence[Release]) -> int:
        """写入新的候选，返回新增数。已有的候选（同一来源）用这次的结果更新标题、体积、做种数和提示，
        例如在 Jackett 中关掉 Strip Cyrillic Letters 之后，再搜一次标题就恢复完整。"""
        added = 0
        for release in releases:
            verdict = classify(release.title, release.size, release.seeders)
            record_id = hashlib.sha1(f"{release.indexer}\n{release.guid}".encode()).hexdigest()[:16]
            existing = self.store.get(record_id) or (self.store.by_hash(release.info_hash) if release.info_hash else None)
            if existing is not None:
                if existing.source == release.indexer:
                    self._refresh(existing, release, verdict)
                continue
            if not verdict.accepted:
                continue
            self.store.save(
                Record(
                    id=record_id,
                    title=release.title,
                    source=release.indexer,
                    size=release.size,
                    published=release.published,
                    details_url=release.details_url,
                    download_url=release.download_url,
                    magnet=release.magnet,
                    info_hash=release.info_hash,
                    seeders=release.seeders,
                    kind=verdict.kind,
                    discs=verdict.discs,
                    warnings=verdict.notes,
                    labels=verdict.labels,
                )
            )
            added += 1
        return added

    def _refresh(self, record: Record, release: Release, verdict: Verdict) -> None:
        """只更新还没推送的候选；新标题不再符合过滤条件时移到“已忽略”。"""
        if record.status not in ("new", "ignored"):
            return
        changes: dict[str, Any] = {
            "title": release.title,
            "size": release.size or record.size,
            "seeders": release.seeders,
            "download_url": release.download_url or record.download_url,
            "magnet": release.magnet or record.magnet,
        }
        if verdict.accepted:
            changes.update(kind=verdict.kind, discs=verdict.discs, warnings=verdict.notes, labels=verdict.labels)
        elif record.status == "new":
            changes.update(status="ignored", error=f"重新搜索后不符合过滤条件：{verdict.reason}")
        if any(getattr(record, key) != value for key, value in changes.items()):
            self.store.update(record.id, **changes)

    def _quick_steps(self, sources: set[str]) -> list[Step]:
        """日常搜索：每个关键词只读第 1 页（最新的资源在最前面）。"""
        steps: list[Step] = []
        jackett, rutor = self.jackett, self.rutor
        if "jackett" in sources and jackett is not None and self.config.jackett is not None:
            steps += [(f"Jackett「{q}」", functools.partial(jackett.search, q)) for q in self.config.jackett.queries]
        if "rutor" in sources and rutor is not None and self.config.rutor is not None:
            films = self.config.rutor.films_only
            steps += [(f"rutor「{q}」", _rutor_step(rutor, q, films, False)) for q in self.config.rutor.queries]
        return steps

    def _backfill_steps(self) -> list[Step]:
        """全面搜索。Jackett：关键词 × 年份；rutor 直连：关键词本身和关键词 × 年份，每次都翻完所有页。"""
        steps: list[Step] = []
        jackett, rutor = self.jackett, self.rutor
        if jackett is not None and self.config.jackett is not None:
            steps += [
                (f"Jackett「{q}」", functools.partial(jackett.search, q)) for q in backfill_queries(self.config.jackett.queries)
            ]
        if rutor is not None and self.config.rutor is not None:
            queries = [*self.config.rutor.queries, *backfill_queries(self.config.rutor.queries)]
            films = self.config.rutor.films_only
            steps += [(f"rutor「{q}」", _rutor_step(rutor, q, films, True)) for q in queries]
        return steps

    def _run_steps(
        self, steps: Sequence[Step], progress: Callable[[int, int], None] | None = None, kind: str = "日常搜索"
    ) -> tuple[int, list[str]]:
        """逐个执行，单次失败不影响其余；连续失败 MAX_CONSECUTIVE_FAILURES 次时停止。返回（新增数，错误）。
        按来源记下这次搜索的情况，显示在资源页上，便于判断新资源有没有搜到。"""
        added = 0
        errors: list[str] = []
        consecutive = 0
        stats: dict[str, dict[str, Any]] = {}
        for index, (label, run) in enumerate(steps, start=1):
            source = label.split("「", 1)[0]
            stat = stats.setdefault(source, {"kind": kind, "last": time.time(), "results": 0, "added": 0,
                                             "newest": None, "error": None})
            try:
                releases = run()
                new = self._ingest(releases)
                added += new
                stat["results"] += len(releases)
                stat["added"] += new
                dates = [r.published for r in releases if r.published]
                if dates and (stat["newest"] is None or max(dates) > stat["newest"]):
                    stat["newest"] = max(dates)
                consecutive = 0
            except IndexerError as error:
                errors.append(f"{label}：{error}")
                stat["error"] = f"{label}：{error}"
                consecutive += 1
            if progress is not None:
                progress(index, added)
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                errors.append(f"连续 {consecutive} 次失败，停止搜索")
                break
        self.state.sources.update(stats)
        return added, errors

    @staticmethod
    def _summary(errors: Sequence[str], total: int) -> str | None:
        if not errors:
            return None
        failed = len([e for e in errors if not e.startswith("连续")])
        return f"{failed}/{total} 次搜索失败。{errors[0]}" + (f"（{errors[-1]}）" if errors[-1].startswith("连续") else "")

    async def search(self, sources: set[str] | None = None) -> int:
        """sources：jackett、rutor，默认全部已配置的来源。全部失败时抛出 WatcherError。"""
        steps = self._quick_steps(sources or {"jackett", "rutor"})
        if not steps:
            raise WatcherError("没有配置 Jackett 或 rutor 直连")
        async with self._search_lock:
            try:
                added, errors = await asyncio.to_thread(self._run_steps, steps)
            finally:
                self.state.last_search = time.time()
            self.state.search_error = self._summary(errors, len(steps))
            self.state.last_added = added
            if errors and len([e for e in errors if not e.startswith("连续")]) >= len(steps):
                raise WatcherError(self.state.search_error or "搜索失败")
            return added

    def start_backfill(self) -> int:
        """在后台全面搜索，返回搜索次数。同一时间只能有一个搜索。"""
        steps = self._backfill_steps()
        if not steps:
            raise WatcherError("没有配置 Jackett 或 rutor 直连")
        if self._search_lock.locked() or self.state.backfill_running:
            raise WatcherError("正在搜索，请等这次搜索完成")
        self.state.backfill_running = True
        self.state.backfill_done = self.state.backfill_added = 0
        self.state.backfill_total = len(steps)
        self._backfill_task = asyncio.get_running_loop().create_task(self._backfill(steps))
        return len(steps)

    async def _backfill(self, steps: Sequence[Step]) -> None:
        def progress(done: int, added: int) -> None:
            self.state.backfill_done, self.state.backfill_added = done, added

        async with self._search_lock:
            try:
                added, errors = await asyncio.to_thread(self._run_steps, steps, progress, "全面搜索")
                self.state.search_error = self._summary(errors, len(steps))
                if errors:
                    log.warning("全面搜索：%s", self.state.search_error)
                log.info("全面搜索完成，新增 %d 个候选", added)
            finally:
                self.state.backfill_running = False
                self.state.last_backfill = time.time()

    # ---------- 推送到 qB ----------

    def _download(self, record: Record) -> Record:
        assert self.qbit is not None and self.config.qbit is not None
        qb = self.config.qbit
        torrent: bytes | None = None
        magnet = record.magnet
        fetch: Callable[[str], bytes | str] | None = None
        if record.source == RUTOR_SOURCE and self.rutor is not None:
            fetch = self.rutor.fetch  # d.rutor.info，不需要登录
        elif self.jackett is not None:
            fetch = self.jackett.fetch
        if record.download_url and fetch is not None:
            try:
                fetched = fetch(record.download_url)
            except IndexerError:
                if not magnet:
                    raise
            else:
                torrent, magnet = (fetched, None) if isinstance(fetched, bytes) else (None, fetched)
        if torrent is not None:
            info_hash = torrent_info_hash(torrent)
            # 网页标题没写、种子里的文件夹名却写着 Custom 等标记的盘：拒绝推送，移到“已忽略”
            if reason := inspect_contents(*torrent_contents(torrent)):
                self.store.update(record.id, status="ignored", error=reason, warnings=[reason, *record.warnings])
                raise WatcherError(f"拒绝推送：{reason}")
        elif magnet:
            info_hash = magnet_info_hash(magnet) or record.info_hash or ""
        else:
            raise WatcherError("没有可用的种子文件或磁力链接")
        if not info_hash:
            raise WatcherError("磁力链接中没有 info hash")
        existing = self.store.by_hash(info_hash)
        if existing is not None and existing.id != record.id:
            raise WatcherError(f"这个种子已经在列表中：{existing.title}")
        self.qbit.ensure_category(qb.category)
        if torrent is not None:
            self.qbit.add(torrent=torrent, category=qb.category, save_path=qb.save_path, tags=["whatdvd"])
        else:
            self.qbit.add(magnet=magnet, category=qb.category, save_path=qb.save_path, tags=["whatdvd"])
        return self.store.update(record.id, status="sent", info_hash=info_hash, progress=0.0, error=None)

    async def download(self, record_id: str) -> Record:
        if self.qbit is None:
            raise WatcherError("没有配置 qBittorrent")
        record = self.store.get(record_id)
        if record is None:
            raise WatcherError("找不到这个资源")
        if record.status not in ("new", "ignored", "failed") or (record.status == "failed" and record.local_path):
            raise WatcherError("这个资源已经推送过了")
        try:
            return await asyncio.to_thread(self._download, record)
        except (IndexerError, QbitError) as error:
            raise WatcherError(str(error)) from None

    def _seed(self, torrent: bytes, data: Path) -> tuple[bool, str]:
        assert self.qbit is not None and self.config.qbit is not None
        category = self.config.qbit.seed_category
        save_path = self._path_map.to_remote(data.parent)
        self.qbit.ensure_category(category)
        added = self.qbit.add(torrent=torrent, category=category, save_path=save_path, tags=["whatdvd-seed"], seeding=True)
        return added, save_path

    async def seed(self, torrent: bytes, data: Path) -> tuple[bool, str]:
        """把我们做的种子添加到 qB 做种：数据就在 data（发种目录中的盘），跳过校验。
        用单独的分类，不会被当成新下载再处理一遍。返回（是否新添加，qB 中的保存路径）。"""
        if self.qbit is None:
            raise WatcherError("没有配置 qBittorrent")
        try:
            return await asyncio.to_thread(self._seed, torrent, data)
        except QbitError as error:
            raise WatcherError(str(error)) from None

    # ---------- 跟踪下载、自动处理 ----------

    def _allowed(self, path: Path) -> Path | None:
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        return resolved if any(resolved.is_relative_to(root) for root in self.config.roots) else None

    def _start(self, record: Record, local: Path) -> None:
        """local 为本机路径（已做过路径映射）。"""
        # 只有磁力链接、推送前看不到种子内容的，以及在 qB 中手动加进分类的：按下载下来的文件夹再查一次
        if local.exists() and (reason := inspect_contents(local.name, _relative_files(local))):
            self.store.update(record.id, status="failed", progress=1.0, local_path=str(local),
                              error=f"不处理：{reason}")
            return
        resolved = self._allowed(local)
        if resolved is None:
            hint = "路径不存在" if not local.exists() else "不在允许的目录（roots）内"
            self.store.update(
                record.id,
                status="failed",
                progress=1.0,
                local_path=str(local),
                error=f"下载完成，但 {local} {hint}。请检查 qbittorrent.path_map 和 roots。",
            )
            return
        job = self._submit_run(resolved, record.title)
        self.store.update(
            record.id,
            status="processing",
            progress=1.0,
            local_path=str(resolved),
            job_id=job.id,
            output_dir=str(job.output_dir),
            error=None,
        )

    async def sync(self) -> None:
        if self.qbit is None or self.config.qbit is None:
            return
        async with self._sync_lock:
            try:
                torrents = await asyncio.to_thread(self.qbit.torrents, category=self.config.qbit.category)
            except QbitError as error:
                self.state.sync_error = str(error)
                raise WatcherError(str(error)) from None
            finally:
                self.state.last_sync = time.time()
            self.state.sync_error = None
            by_hash = {t.hash: t for t in torrents}

            # 在 qB 中手动添加到分类的种子
            for torrent in torrents:
                if self.store.by_hash(torrent.hash) is None:
                    self.store.save(
                        Record(
                            id=f"qb-{torrent.hash[:16]}",
                            title=torrent.name,
                            source="qBittorrent",
                            status="sent",
                            size=torrent.size,
                            info_hash=torrent.hash,
                        )
                    )

            for record in self.store.list(ACTIVE):
                found = by_hash.get(record.info_hash or "")
                if found is None:
                    self.store.update(record.id, status="failed", error="qBittorrent 中找不到这个种子（已删除或改了分类）")
                elif not found.completed:
                    if (record.status, record.progress) != ("downloading", found.progress):
                        self.store.update(record.id, status="downloading", progress=found.progress)
                else:
                    self._start(record, self._path_map.to_local(found.content_path))

            for record in self.store.list(["processing"]):
                job = self._get_job(record.job_id or "")
                if job is None:
                    self.store.update(record.id, status="failed", error="服务重启，处理中断。可以点“重新处理”。")
                elif job.finished:
                    result = job.result or {}
                    post_file = result.get("post_file")
                    if job.status == "done" and post_file:
                        self.store.update(record.id, status="done", post_file=post_file, error=None)
                    else:
                        message = job.error or "有截图或上传失败，没有生成发布说明，详见任务日志"
                        self.store.update(record.id, status="failed", post_file=post_file, error=message)

    async def reprocess(self, record_id: str) -> Record:
        record = self.store.get(record_id)
        if record is None:
            raise WatcherError("找不到这个资源")
        if record.status != "failed" or not record.local_path:
            raise WatcherError("只有下载完成后处理失败的资源可以重新处理")
        self._start(record, Path(record.local_path))
        updated = self.store.get(record_id)
        assert updated is not None
        return updated

    # ---------- 循环 ----------

    async def run_forever(self) -> None:
        qb_interval = self.config.qbit.interval if self.config.qbit else 60
        schedule = {  # 来源 → 间隔（秒），0 为只手动搜索
            "Jackett": self.config.jackett.interval * 60 if self.config.jackett and self.jackett else None,
            "rutor": self.config.rutor.interval * 60 if self.config.rutor and self.rutor else None,
        }
        names = {"Jackett": "jackett", "rutor": "rutor"}
        due = {source: time.monotonic() for source in schedule}
        while True:
            if self.qbit is not None:
                try:
                    await self.sync()
                except WatcherError as error:
                    log.warning("检查 qBittorrent 失败：%s", error)
            for source, every in schedule.items():
                if every is None:
                    continue
                if every and time.monotonic() >= due[source] and not self.state.backfill_running:
                    due[source] = time.monotonic() + every
                    try:
                        added = await self.search({names[source]})
                        log.info("%s 搜索完成，新增 %d 个候选", source, added)
                    except WatcherError as error:
                        log.warning("%s 搜索失败：%s", source, error)
                self.state.schedule[source] = {
                    "every": every // 60,
                    "next": time.time() + max(due[source] - time.monotonic(), 0) if every else None,
                }
            await asyncio.sleep(qb_interval if self.qbit is not None else 60)
