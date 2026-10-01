"""后台轮询：定时用 Jackett 搜索 DVD 原盘写入候选；跟踪 qBittorrent 中的下载，完成后自动处理。

候选只有用户点了“下载”才会推送到 qB。在 qB 中手动添加到同一分类的种子，下载完成后也会自动处理。
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..indexer import IndexerError, Jackett, Release, classify
from ..qbit import PathMap, QBittorrent, QbitError, magnet_info_hash, torrent_info_hash
from ..store import Record, Status, Store
from .config import ServerConfig
from .jobs import Job

log = logging.getLogger("whatdvd.watcher")

ACTIVE: tuple[Status, ...] = ("sent", "downloading")

BACKFILL_FROM = 1920
"""全面搜索从这一年开始。站点每次搜索最多返回 100 条，按“关键词 年份”拆开才能搜到更早的资源。"""


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


class Watcher:
    def __init__(
        self,
        config: ServerConfig,
        store: Store,
        *,
        submit_run: Callable[[Path], Job],
        get_job: Callable[[str], Job | None],
        qbit: QBittorrent | None = None,
        jackett: Jackett | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.qbit = qbit
        self.jackett = jackett
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
            "qbit": self.qbit is not None,
            "last_search": self.state.last_search,
            "search_error": self.state.search_error,
            "last_added": self.state.last_added,
            "searching": self._search_lock.locked(),
            "last_sync": self.state.last_sync,
            "sync_error": self.state.sync_error,
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

    # ---------- 搜索 ----------

    def _ingest(self, releases: Sequence[Release]) -> int:
        added = 0
        for release in releases:
            verdict = classify(release.title, release.size, release.seeders)
            if not verdict.accepted:
                continue
            record = Record(
                id=hashlib.sha1(f"{release.indexer}\n{release.guid}".encode()).hexdigest()[:16],
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
            )
            added += self.store.add_new(record)
        return added

    def _search(self, queries: Sequence[str], progress: Callable[[int, int], None] | None = None) -> int:
        assert self.jackett is not None
        added = 0
        for index, query in enumerate(queries, start=1):
            added += self._ingest(self.jackett.search(query))
            if progress is not None:
                progress(index, added)
        return added

    async def search(self) -> int:
        if self.jackett is None:
            raise WatcherError("没有配置 Jackett")
        assert self.config.jackett is not None
        async with self._search_lock:
            try:
                added = await asyncio.to_thread(self._search, self.config.jackett.queries)
            except IndexerError as error:
                self.state.search_error = str(error)
                raise WatcherError(str(error)) from None
            finally:
                self.state.last_search = time.time()
            self.state.search_error = None
            self.state.last_added = added
            return added

    def start_backfill(self) -> int:
        """在后台按“关键词 年份”逐个搜索，返回要搜索的次数。同一时间只能有一个搜索。"""
        if self.jackett is None or self.config.jackett is None:
            raise WatcherError("没有配置 Jackett")
        if self._search_lock.locked() or self.state.backfill_running:
            raise WatcherError("正在搜索，请等这次搜索完成")
        queries = backfill_queries(self.config.jackett.queries)
        self.state.backfill_running = True
        self.state.backfill_done = self.state.backfill_added = 0
        self.state.backfill_total = len(queries)
        self._backfill_task = asyncio.get_running_loop().create_task(self._backfill(queries))
        return len(queries)

    async def _backfill(self, queries: Sequence[str]) -> None:
        def progress(done: int, added: int) -> None:
            self.state.backfill_done, self.state.backfill_added = done, added

        async with self._search_lock:
            try:
                added = await asyncio.to_thread(self._search, queries, progress)
            except IndexerError as error:
                self.state.search_error = f"全面搜索在第 {self.state.backfill_done + 1} 次查询时失败：{error}"
                log.warning("%s", self.state.search_error)
            else:
                self.state.search_error = None
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
        if record.download_url and self.jackett is not None:
            try:
                fetched = self.jackett.fetch(record.download_url)
            except IndexerError:
                if not magnet:
                    raise
            else:
                torrent, magnet = (fetched, None) if isinstance(fetched, bytes) else (None, fetched)
        if torrent is not None:
            info_hash = torrent_info_hash(torrent)
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

    # ---------- 跟踪下载、自动处理 ----------

    def _allowed(self, path: Path) -> Path | None:
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        return resolved if any(resolved.is_relative_to(root) for root in self.config.roots) else None

    def _start(self, record: Record, local: Path) -> None:
        """local 为本机路径（已做过路径映射）。"""
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
        job = self._submit_run(resolved)
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
        search_every = self.config.jackett.interval * 60 if self.config.jackett else 0
        next_search = time.monotonic()
        while True:
            if self.qbit is not None:
                try:
                    await self.sync()
                except WatcherError as error:
                    log.warning("检查 qBittorrent 失败：%s", error)
            if self.jackett is not None and search_every and time.monotonic() >= next_search:
                next_search = time.monotonic() + search_every
                try:
                    added = await self.search()
                    log.info("Jackett 搜索完成，新增 %d 个候选", added)
                except WatcherError as error:
                    log.warning("Jackett 搜索失败：%s", error)
            await asyncio.sleep(qb_interval)
