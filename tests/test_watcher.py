"""后台轮询：模拟的 Jackett 与 qBittorrent，不访问网络。"""

import asyncio
import datetime
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from conftest import make_file
from test_qbit import TORRENT
from whatdvd.indexer import Jackett
from whatdvd.qbit import QBittorrent, torrent_info_hash
from whatdvd.store import Store
from whatdvd.web.config import JackettConfig, QbitConfig, ServerConfig
from whatdvd.web.jobs import Job
from whatdvd.web.watcher import Watcher, WatcherError

TORRENT_HASH = torrent_info_hash(TORRENT)


def _item(guid: str, title: str, size: int, infohash: str = "") -> str:
    attr = f'<torznab:attr name="infohash" value="{infohash}" />' if infohash else ""
    return (
        f"<item><title>{title}</title><guid>{guid}</guid><jackettindexer id='rutor'>RuTor</jackettindexer>"
        f"<size>{size}</size><link>http://jackett/dl/rutor/?path={guid}</link>"
        f'<torznab:attr name="seeders" value="2" />{attr}</item>'
    )


FEED = (
    '<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><title>RuTor</title>'
    + _item("1", "Film One (2001) DVD9", 7_000_000_000)
    + _item("2", "Film Two (2002) DVD9 | P -Custom", 7_000_000_000)
    + _item("3", "Film Three (2003) DVDRip", 1_000_000_000)
    + _item("4", "Film Four (2004) 2 DVD5 | P2", 8_000_000_000)
    + "</channel></rss>"
)


class FakeServices:
    """一个 MockTransport 同时模拟 Jackett 和 qBittorrent。"""

    def __init__(self) -> None:
        self.torrents: list[dict[str, Any]] = []
        self.added: list[bytes] = []
        self.categories: list[str] = []
        self.qb_down = False
        self.dl_fails = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "jackett":
            if path.endswith("/torznab/api"):
                return httpx.Response(200, text=FEED)
            if path.startswith("/dl/"):
                return httpx.Response(500) if self.dl_fails else httpx.Response(200, content=TORRENT)
        if host == "qb":
            if self.qb_down:
                raise httpx.ConnectError("refused", request=request)
            if path == "/api/v2/auth/login":
                return httpx.Response(204, headers={"set-cookie": "QBT_SID_8080=s; path=/"})
            if path == "/api/v2/torrents/createCategory":
                self.categories.append(request.content.decode())
                return httpx.Response(200)
            if path == "/api/v2/torrents/add":
                self.added.append(request.content)
                return httpx.Response(200, text="Ok.")
            if path == "/api/v2/torrents/info":
                return httpx.Response(200, json=self.torrents)
        return httpx.Response(404)

    def torrent(self, info_hash: str, state: str, progress: float, path: str, name: str = "Film") -> None:
        self.torrents = [t for t in self.torrents if t["hash"] != info_hash] + [
            {"hash": info_hash, "name": name, "state": state, "progress": progress, "content_path": path, "size": 1}
        ]


@pytest.fixture
def downloads(tmp_path: Path) -> Path:
    root = tmp_path / "media"
    root.mkdir()
    return root.resolve()


@pytest.fixture
def services() -> FakeServices:
    return FakeServices()


class Harness:
    def __init__(self, tmp_path: Path, downloads: Path, services: FakeServices) -> None:
        transport = httpx.MockTransport(services)
        self.config = ServerConfig(
            roots=(downloads,),
            output_dir=tmp_path / "out",
            token="t",
            qbit=QbitConfig("http://qb:8080", "admin", "pw", category="whatdvd", path_map=(("/downloads", str(downloads)),)),
            jackett=JackettConfig("http://jackett", "KEY", indexer="rutor", queries=("DVD9", "DVD5")),
        )
        self.jobs: dict[str, Job] = {}
        self.submitted: list[Path] = []
        self.watcher = Watcher(
            self.config,
            Store(tmp_path / "state.db"),
            submit_run=self.submit,
            get_job=self.jobs.get,
            qbit=QBittorrent("http://qb:8080", "admin", "pw", transport=transport),
            jackett=Jackett("http://jackett", "KEY", indexer="rutor", transport=transport),
        )

    def submit(self, path: Path) -> Job:
        self.submitted.append(path)
        job = Job("run", path, {"count": 10, "upload": True}, self.config.output_dir / path.name)
        self.jobs[job.id] = job
        return job

    def run(self, call: Callable[[], Any]) -> Any:
        return asyncio.run(call())


@pytest.fixture
def h(tmp_path: Path, downloads: Path, services: FakeServices) -> Harness:
    return Harness(tmp_path, downloads, services)


def test_search_keeps_only_accepted_and_dedupes(h: Harness) -> None:
    assert h.run(h.watcher.search) == 2  # Custom 和 DVDRip 被排除；两个查询返回同样的结果，第二次不重复
    titles = {r.title: r for r in h.watcher.store.list(["new"])}
    assert set(titles) == {"Film One (2001) DVD9", "Film Four (2004) 2 DVD5 | P2"}
    four = titles["Film Four (2004) 2 DVD5 | P2"]
    assert (four.kind, four.discs, four.source, four.seeders) == ("2×DVD5", 2, "RuTor", 2)
    assert any("配音" in w for w in four.warnings)
    assert h.run(h.watcher.search) == 0
    assert h.watcher.status()["last_added"] == 0


def test_search_error_is_recorded(h: Harness, services: FakeServices) -> None:
    h.watcher.jackett = Jackett("http://jackett", "KEY", transport=httpx.MockTransport(lambda r: httpx.Response(502)))
    with pytest.raises(WatcherError, match="HTTP 502"):
        h.run(h.watcher.search)
    assert "HTTP 502" in h.watcher.status()["search_error"]


def _first(h: Harness) -> str:
    h.run(h.watcher.search)
    return next(r.id for r in h.watcher.store.list(["new"]) if r.title.startswith("Film One"))


def test_download_pushes_torrent_file(h: Harness, services: FakeServices) -> None:
    record_id = _first(h)
    record = h.run(lambda: h.watcher.download(record_id))
    assert (record.status, record.info_hash) == ("sent", TORRENT_HASH)
    assert services.categories == ["category=whatdvd&savePath="]
    body = services.added[0]
    assert b'filename="whatdvd.torrent"' in body and b"whatdvd" in body
    with pytest.raises(WatcherError, match="已经推送过"):
        h.run(lambda: h.watcher.download(record_id))


def test_download_falls_back_to_magnet(h: Harness, services: FakeServices) -> None:
    services.dl_fails = True
    record_id = _first(h)
    h.watcher.store.update(record_id, magnet="magnet:?xt=urn:btih:" + "b" * 40)
    record = h.run(lambda: h.watcher.download(record_id))
    assert record.info_hash == "b" * 40
    assert b"magnet%3A%3Fxt%3Durn%3Abtih%3A" + b"b" * 40 in services.added[0]


def test_download_without_qbit(h: Harness) -> None:
    record_id = _first(h)
    h.watcher.qbit = None
    with pytest.raises(WatcherError, match="没有配置 qBittorrent"):
        h.run(lambda: h.watcher.download(record_id))


def test_sync_progress_then_process_then_done(h: Harness, services: FakeServices, downloads: Path) -> None:
    record_id = _first(h)
    h.run(lambda: h.watcher.download(record_id))
    services.torrent(TORRENT_HASH, "downloading", 0.42, "/downloads/Film One")
    h.run(h.watcher.sync)
    record = h.watcher.store.get(record_id)
    assert record is not None and (record.status, record.progress) == ("downloading", 0.42)

    make_file(downloads / "Film One" / "VIDEO_TS" / "VTS_01_1.VOB", 1)
    services.torrent(TORRENT_HASH, "stalledUP", 1.0, "/downloads/Film One")
    h.run(h.watcher.sync)
    assert h.submitted == [downloads / "Film One"]  # 按 path_map 映射到本机路径
    record = h.watcher.store.get(record_id)
    assert record is not None and record.status == "processing" and record.job_id in h.jobs

    h.run(h.watcher.sync)  # 任务还在跑：不重复提交
    assert len(h.submitted) == 1

    job = h.jobs[record.job_id]
    job.status, job.result = "done", {"ok": True, "post_file": "Film.One.post.txt"}
    h.run(h.watcher.sync)
    record = h.watcher.store.get(record_id)
    assert record is not None and (record.status, record.post_file, record.error) == ("done", "Film.One.post.txt", None)


def test_failed_job_and_restart(h: Harness, services: FakeServices, downloads: Path) -> None:
    make_file(downloads / "Film" / "VIDEO_TS" / "VTS_01_1.VOB", 1)
    services.torrent("c" * 40, "uploading", 1.0, "/downloads/Film")
    h.run(h.watcher.sync)  # 在 qB 中手动添加到分类的种子
    record = h.watcher.store.by_hash("c" * 40)
    assert record is not None and (record.source, record.status) == ("qBittorrent", "processing")

    job = h.jobs[record.job_id or ""]
    job.status, job.result = "done", {"ok": False, "post_file": None}
    h.run(h.watcher.sync)
    record = h.watcher.store.by_hash("c" * 40)
    assert record is not None and record.status == "failed" and "没有生成发布说明" in (record.error or "")

    record = h.run(lambda: h.watcher.reprocess(record.id))
    assert record.status == "processing" and h.submitted[-1] == downloads / "Film"
    h.jobs.clear()  # 服务重启，内存中的任务没了
    h.run(h.watcher.sync)
    record = h.watcher.store.by_hash("c" * 40)
    assert record is not None and record.status == "failed" and "服务重启" in (record.error or "")


def test_completed_outside_roots_or_missing(h: Harness, services: FakeServices, tmp_path: Path) -> None:
    services.torrent("d" * 40, "stalledUP", 1.0, "/elsewhere/Film")
    h.run(h.watcher.sync)
    record = h.watcher.store.by_hash("d" * 40)
    assert record is not None and record.status == "failed"
    assert "路径不存在" in (record.error or "") and "path_map" in (record.error or "")
    assert h.submitted == []

    outside = tmp_path / "outside"
    outside.mkdir()
    services.torrent("e" * 40, "stalledUP", 1.0, str(outside))
    h.run(h.watcher.sync)
    record = h.watcher.store.by_hash("e" * 40)
    assert record is not None and "不在允许的目录" in (record.error or "")


def test_removed_from_qbit(h: Harness, services: FakeServices) -> None:
    record_id = _first(h)
    h.run(lambda: h.watcher.download(record_id))
    h.run(h.watcher.sync)  # qB 列表里没有这个种子
    record = h.watcher.store.get(record_id)
    assert record is not None and record.status == "failed" and "找不到这个种子" in (record.error or "")
    record = h.run(lambda: h.watcher.download(record_id))  # 可以重新下载
    assert record.status == "sent"


def test_qbit_unreachable(h: Harness, services: FakeServices) -> None:
    services.qb_down = True
    with pytest.raises(WatcherError, match="连不上 qBittorrent"):
        h.run(h.watcher.sync)
    assert "连不上" in h.watcher.status()["sync_error"]


def test_backfill_queries() -> None:
    from whatdvd.web.watcher import BACKFILL_FROM, backfill_queries

    queries = backfill_queries(["DVD9", "DVD5"], until=2026)
    assert queries[:3] == ["DVD9 2026", "DVD5 2026", "DVD9 2025"]  # 近的年份在前
    assert queries[-1] == f"DVD5 {BACKFILL_FROM}"
    assert len(queries) == 2 * (2026 - BACKFILL_FROM + 1)


def test_backfill_runs_in_background(h: Harness, services: FakeServices) -> None:
    seen: list[str] = []
    original = services.__call__

    def record(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/torznab/api"):
            seen.append(request.url.params["q"])
        return original(request)

    h.watcher.jackett = Jackett("http://jackett", "KEY", indexer="rutor", transport=httpx.MockTransport(record))

    async def scenario() -> dict[str, Any]:
        total = h.watcher.start_backfill()
        assert h.watcher.status()["backfill"]["running"]
        with pytest.raises(WatcherError, match="正在搜索"):
            h.watcher.start_backfill()
        task = h.watcher._backfill_task
        assert task is not None
        await task
        assert total == len(seen)
        return h.watcher.status()["backfill"]

    status = asyncio.run(scenario())
    assert status["running"] is False and status["done"] == status["total"] and status["added"] == 2
    assert seen[0].startswith("DVD9 ") and seen[0].split()[1].isdigit()
    assert len(h.watcher.store.list(["new"])) == 2


def test_backfill_error_is_reported(h: Harness) -> None:
    h.watcher.jackett = Jackett("http://jackett", "KEY", transport=httpx.MockTransport(lambda r: httpx.Response(502)))

    async def scenario() -> None:
        h.watcher.start_backfill()
        assert h.watcher._backfill_task is not None
        await h.watcher._backfill_task

    asyncio.run(scenario())
    status = h.watcher.status()
    assert status["backfill"]["running"] is False
    # 连续失败 3 次就停止，不再发出剩下的两百多次请求
    assert status["search_error"].startswith("3/") and "连续 3 次失败，停止搜索" in status["search_error"]
    assert status["backfill"]["done"] == 3


# ---------- rutor 直连 ----------


def _rutor_harness(tmp_path: Path, downloads: Path, total: int) -> tuple[Harness, Any]:
    from dataclasses import replace

    from test_rutor import FakeRutor

    from whatdvd.rutor import Rutor
    from whatdvd.web.config import RutorConfig

    services = FakeServices()
    h = Harness(tmp_path, downloads, services)
    fake = FakeRutor(total)
    h.config = replace(h.config, jackett=None, rutor=RutorConfig("https://rutor.info", queries=("DVD9",)))
    h.watcher = Watcher(
        h.config, h.watcher.store, submit_run=h.submit, get_job=h.jobs.get,
        qbit=h.watcher.qbit, rutor=Rutor(delay=0, transport=httpx.MockTransport(fake)),
    )
    return h, fake


def test_rutor_quick_search_reads_first_page_only(tmp_path: Path, downloads: Path) -> None:
    h, fake = _rutor_harness(tmp_path, downloads, total=250)
    assert h.run(h.watcher.search) == 100
    assert fake.requests == ["/search/0/0/100/0/DVD9"]
    record = h.watcher.store.list(["new"])[0]
    assert record.source == "rutor 直连" and record.kind == "DVD9"
    assert h.watcher.status()["rutor"] is True


def test_rutor_backfill_reads_all_pages_of_keyword_and_years(tmp_path: Path, downloads: Path) -> None:
    h, fake = _rutor_harness(tmp_path, downloads, total=150)

    async def scenario() -> int:
        total = h.watcher.start_backfill()
        assert h.watcher._backfill_task is not None
        await h.watcher._backfill_task
        return total

    from whatdvd.web.watcher import BACKFILL_FROM

    steps = h.run(scenario)
    assert steps == 1 + (datetime.date.today().year - BACKFILL_FROM + 1)  # 关键词本身 + 每个年份
    assert fake.requests[:2] == ["/search/0/0/100/0/DVD9", "/search/1/0/100/0/DVD9"]  # 每次都翻页
    assert len(fake.requests) == steps * 2
    assert h.watcher.status()["backfill"]["added"] == 150  # 每次返回同样的 150 条，只新增一次


def test_rutor_download_uses_rutor_torrent_file(tmp_path: Path, downloads: Path) -> None:
    h, fake = _rutor_harness(tmp_path, downloads, total=1)
    h.run(h.watcher.search)
    record = h.watcher.store.list(["new"])[0]
    pushed = h.run(lambda: h.watcher.download(record.id))
    assert pushed.status == "sent"
    assert fake.requests[-1] == "/download/1"  # 从 d.rutor.info 取种子，不经过 Jackett


def test_same_torrent_from_jackett_and_rutor_is_listed_once(tmp_path: Path, downloads: Path) -> None:
    h, _ = _rutor_harness(tmp_path, downloads, total=1)
    h.run(h.watcher.search)
    from whatdvd.indexer import Release

    same = Release(guid="https://d.rutor.info/download/1", indexer="RuTor", title="Film 0 DVD9", size=1,
                   published=None, details_url=None, download_url=None, magnet=None, info_hash=f"{1:040x}", seeders=1)
    assert h.watcher._ingest([same]) == 0


def test_search_again_refreshes_existing_candidates(h: Harness, services: FakeServices) -> None:
    """Jackett 关掉 Strip Cyrillic Letters 之后，再搜一次标题恢复完整；露出排除标记的移到已忽略。"""
    from whatdvd.indexer import Release

    def release(guid: str, title: str, seeders: int = 1) -> Release:
        return Release(guid=guid, indexer="Kinozal (M)", title=title, size=4_000_000_000, published=None,
                       details_url=None, download_url=None, magnet=None, info_hash=None, seeders=seeders)

    assert h.watcher._ingest([release("1", "DVD-5"), release("2", "  DVD-5")]) == 2
    first = h.watcher.store.list(["new"])[0]
    assert any("Strip Cyrillic" in w for w in first.warnings)

    assert h.watcher._ingest([release("1", "Фильм / Film 2005 DUB DVD-5", seeders=7),
                              release("2", "Фильм / Film 2001 DVD-5-Сжатый")]) == 0
    refreshed = {r.title: r for r in h.watcher.store.list()}
    good = refreshed["Фильм / Film 2005 DUB DVD-5"]
    assert good.status == "new" and good.seeders == 7
    assert not any("Strip Cyrillic" in w for w in good.warnings) and any("Dub" in w for w in good.warnings)
    bad = refreshed["Фильм / Film 2001 DVD-5-Сжатый"]
    assert bad.status == "ignored" and "压缩过的盘" in (bad.error or "")


def test_refresh_leaves_pushed_and_other_source_alone(h: Harness, services: FakeServices) -> None:
    record_id = _first(h)
    h.run(lambda: h.watcher.download(record_id))
    from whatdvd.indexer import Release

    record = h.watcher.store.get(record_id)
    assert record is not None
    same = Release(guid="1", indexer="RuTor", title="Changed DVD9", size=1, published=None, details_url=None,
                   download_url=None, magnet=None, info_hash=None, seeders=1)
    h.watcher._ingest([same])
    after = h.watcher.store.get(record_id)
    assert after is not None and after.title == record.title  # 已推送的不改
