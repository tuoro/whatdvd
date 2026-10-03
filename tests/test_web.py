from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import FakeRunner, make_file, ok
from whatdvd.runner import CommandResult
from whatdvd.web.app import COOKIE, create_app
from whatdvd.web.config import ServerConfig
from whatdvd.web.jobs import Job, JobManager, JobReporter

TOKEN = "test-token"


def fake_mktorrent(argv: tuple[str, ...]) -> CommandResult:
    if argv[0] == "mktorrent":
        Path(argv[argv.index("-o") + 1]).write_bytes(b"d8:announce0:e")
    return ok(argv)


@pytest.fixture
def media(tmp_path: Path) -> Path:
    root = tmp_path / "media"
    make_file(root / "Movie A" / "VIDEO_TS" / "VTS_01_1.VOB", 10)
    make_file(root / "Movie B.iso", 10)
    make_file(root / "Plain" / "readme.txt", 1)
    make_file(root / ".hidden" / "x", 1)
    make_file(root / "notes.txt", 1)
    make_file(tmp_path / "outside" / "secret.iso", 1)
    (root / "escape").symlink_to(tmp_path / "outside")
    return root


@pytest.fixture
def client(tmp_path: Path, media: Path) -> Iterator[TestClient]:
    config = ServerConfig(roots=(media.resolve(),), output_dir=tmp_path / "out", token=TOKEN)
    app = create_app(config, runner=FakeRunner(fake_mktorrent, available=["mktorrent"]))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def authed(client: TestClient) -> TestClient:
    assert client.post("/api/login", json={"token": TOKEN}).status_code == 204
    return client


def wait_job(client: TestClient, job_id: str, timeout: float = 30) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job: dict[str, Any] = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("任务超时")


# ---------- 认证 ----------


def test_requires_login(client: TestClient) -> None:
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/browse").status_code == 401
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/").status_code == 200  # 登录页本身公开


def test_login_sets_strict_httponly_cookie(client: TestClient) -> None:
    assert client.post("/api/login", json={"token": "wrong"}).status_code == 401
    response = client.post("/api/login", json={"token": TOKEN})
    assert response.status_code == 204
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/config").status_code == 200
    assert client.post("/api/logout").status_code == 204
    client.cookies.clear()
    assert client.get("/api/config").status_code == 401


def test_bearer_token(client: TestClient) -> None:
    assert client.get("/api/config", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert client.get("/api/config", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_token_link_sets_cookie_and_redirects(client: TestClient) -> None:
    response = client.get(f"/?token={TOKEN}", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert COOKIE in response.headers["set-cookie"]
    bad = client.get("/?token=nope", follow_redirects=False)
    assert bad.status_code == 303 and "set-cookie" not in bad.headers


def test_assets_are_versioned(client: TestClient) -> None:
    """升级后浏览器不会继续用缓存里的旧脚本：地址带内容哈希，并且每次都要向服务器确认。"""
    import re

    page = client.get("/").text
    script = re.search(r'src="(/static/app\.js\?v=[0-9a-f]{12})"', page)
    style = re.search(r'href="(/static/style\.css\?v=[0-9a-f]{12})"', page)
    assert script and style
    response = client.get(script.group(1))
    assert response.status_code == 200 and response.headers["cache-control"] == "no-cache"
    assert "imdbSection" in response.text
    assert client.get(style.group(1)).headers["cache-control"] == "no-cache"


def test_security_headers(client: TestClient) -> None:
    headers = client.get("/").headers
    assert "default-src 'self'" in headers["content-security-policy"]
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"


# ---------- 目录浏览 ----------


def test_browse_roots_and_entries(authed: TestClient, media: Path) -> None:
    roots = authed.get("/api/browse").json()
    assert roots["path"] is None
    assert [e["path"] for e in roots["entries"]] == [str(media.resolve())]

    listing = authed.get("/api/browse", params={"path": str(media)}).json()
    assert listing["parent"] is None  # 已在根目录
    kinds = {e["name"]: e["kind"] for e in listing["entries"]}
    # 隐藏文件、普通文件和指向允许目录之外的符号链接都不显示；ISO 排在目录后面
    assert kinds == {"Movie A": "dvd", "Plain": "dir", "Movie B.iso": "iso"}
    assert listing["entries"][-1]["name"] == "Movie B.iso"

    inner = authed.get("/api/browse", params={"path": str(media / "Movie A")}).json()
    assert inner["kind"] == "dvd"
    assert inner["parent"] == str(media.resolve())


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/", 403),
        ("{media}/..", 403),
        ("{media}/escape", 403),
        ("{media}/escape/secret.iso", 403),
        ("relative/path", 400),
        ("{media}/missing", 404),
        ("{media}/Movie B.iso", 400),
    ],
)
def test_browse_rejects(authed: TestClient, media: Path, path: str, status: int) -> None:
    response = authed.get("/api/browse", params={"path": path.format(media=media)})
    assert response.status_code == status


# ---------- 任务 ----------


def test_torrent_job_and_download(authed: TestClient, media: Path, tmp_path: Path) -> None:
    response = authed.post(
        "/api/jobs",
        json={"kind": "torrent", "path": str(media / "Movie A"), "announces": ["https://t.example/a", " "], "piece_length": 22},
    )
    assert response.status_code == 201
    job = wait_job(authed, response.json()["id"])
    assert job["status"] == "done" and job["ok"] is True
    assert job["params"] == {"announces": ["https://t.example/a"], "piece_length": 22, "seed_name": ""}
    assert job["result"]["torrent_file"] == "Movie.A.torrent"
    assert any("种子：Movie.A.torrent" in e["message"] for e in job["events"])
    assert (tmp_path / "out" / "Movie.A" / "Movie.A.torrent").is_file()

    download = authed.get(f"/api/jobs/{job['id']}/files/Movie.A.torrent")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/x-bittorrent"
    assert "attachment" in download.headers["content-disposition"]
    assert authed.get(f"/api/jobs/{job['id']}/files/..%2F..%2Fsecret").status_code == 404
    assert authed.get(f"/api/jobs/{job['id']}/files/other.txt").status_code == 404

    jobs = authed.get("/api/jobs").json()
    assert [j["id"] for j in jobs] == [job["id"]]
    assert job["result"]["extra_files"] == []


def test_torrent_job_reports_extra_files(authed: TestClient, media: Path) -> None:
    (media / "Movie A" / "Thumbs.db").write_bytes(b"x")
    body = {"kind": "torrent", "path": str(media / "Movie A"), "announces": [], "piece_length": 24}
    job = wait_job(authed, authed.post("/api/jobs", json=body).json()["id"])
    assert job["status"] == "done" and job["ok"] is True  # 只提示，照样做种
    assert job["result"]["extra_files"] == [{"path": "Thumbs.db", "reason": "系统生成的文件"}]
    messages = [e["message"] for e in job["events"]]
    assert "注意：发现 1 个和上传无关的文件，建议删除后再做种（PTP 2.1.3）：" in messages
    assert (media / "Movie A" / "Thumbs.db").is_file()  # 不删除


def test_events_stream_replays_and_ends(authed: TestClient, media: Path) -> None:
    body = {"kind": "torrent", "path": str(media / "Movie A"), "announces": [], "piece_length": 24}
    job = wait_job(authed, authed.post("/api/jobs", json=body).json()["id"])
    text = authed.get(f"/api/jobs/{job['id']}/events").text
    assert text.count("event: log") == len(job["events"])
    assert "id: 1\n" in text and "event: end" in text
    resumed = authed.get(f"/api/jobs/{job['id']}/events", headers={"Last-Event-ID": "1"}).text
    assert resumed.count("event: log") == len(job["events"]) - 1


def test_failed_job(authed: TestClient, media: Path) -> None:
    body = {"kind": "run", "path": str(media / "Plain"), "count": 3, "upload": False}
    job = wait_job(authed, authed.post("/api/jobs", json=body).json()["id"])
    assert job["status"] == "failed"
    assert "缺少外部命令" in job["error"]  # FakeRunner 里只有 mktorrent


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"kind": "torrent", "path": "/etc", "announces": [], "piece_length": 24}, 403),
        ({"kind": "torrent", "path": "{media}", "announces": ["ftp://x"], "piece_length": 24}, 422),
        ({"kind": "torrent", "path": "{media}", "announces": [], "piece_length": 40}, 422),
        ({"kind": "run", "path": "{media}", "count": 0, "upload": True}, 422),
        ({"kind": "delete", "path": "{media}"}, 422),
    ],
)
def test_job_validation(authed: TestClient, media: Path, body: dict[str, Any], status: int) -> None:
    body = {**body, "path": body["path"].format(media=media)}
    assert authed.post("/api/jobs", json=body).status_code == status


def test_unknown_job(authed: TestClient) -> None:
    assert authed.get("/api/jobs/nope").status_code == 404
    assert authed.get("/api/jobs/nope/events").status_code == 404


# ---------- 任务队列 ----------


def test_job_manager_limits_concurrency_and_records_failure(tmp_path: Path) -> None:
    release = threading.Event()

    def slow(job: Job, reporter: JobReporter) -> dict[str, Any]:
        reporter.info("started")
        release.wait(5)
        return {"ok": True, "files": ["a.png"]}

    def broken(job: Job, reporter: JobReporter) -> dict[str, Any]:
        raise RuntimeError("boom")

    async def scenario() -> tuple[list[str], Job, Job]:
        manager = JobManager(max_jobs=1)
        first = manager.submit(Job("run", tmp_path, {}, tmp_path), slow)
        second = manager.submit(Job("run", tmp_path, {}, tmp_path), broken)
        await asyncio.sleep(0.2)
        states = [first.status, second.status]
        release.set()
        await manager.wait_all()
        return states, first, second

    states, first, second = asyncio.run(scenario())
    assert states == ["running", "queued"]
    assert first.status == "done" and first.files == frozenset({"a.png"})
    assert first.events[0] == {"level": "info", "message": "started"}
    assert second.status == "failed" and second.error == "boom"
    assert second.events[-1] == {"level": "error", "message": "boom"}


# ---------- 来源摘要与进度 ----------


def test_source_summary(authed: TestClient, media: Path) -> None:
    make_file(media / "Movie A" / "VIDEO_TS" / "VTS_01_0.IFO", 5)
    data = authed.get("/api/source", params={"path": str(media)}).json()
    assert data["kind"] == "dir"
    assert [(d["name"], d["kind"], d["media_type"]) for d in data["discs"]] == [
        ("Movie A", "dvd", "DVD5"),
        ("Movie B", "iso", "DVD5"),
    ]
    assert data["discs"][0]["bytes"] == 15
    assert data["total_bytes"] == 25
    empty = authed.get("/api/source", params={"path": str(media / "Plain")}).json()
    assert empty["discs"] == [] and empty["total_bytes"] == 0
    assert authed.get("/api/source", params={"path": "/etc"}).status_code == 403


def test_config_includes_settings_fields(authed: TestClient) -> None:
    data = authed.get("/api/config").json()
    assert data["listen"] == "127.0.0.1:26873"
    assert data["max_jobs"] == 1 and data["custom_template"] is False and data["proxy"] is False


def test_finished_job_has_full_progress(authed: TestClient, media: Path) -> None:
    body = {"kind": "torrent", "path": str(media / "Movie A"), "announces": [], "piece_length": 24}
    job = wait_job(authed, authed.post("/api/jobs", json=body).json()["id"])
    assert job["progress"] == 1.0


# ---------- 资源（Jackett + qBittorrent） ----------


def test_releases_disabled_without_config(authed: TestClient) -> None:
    assert authed.get("/api/releases").status_code == 404
    config = authed.get("/api/config").json()
    assert config["qbit"] is None and config["jackett"] is None


def test_releases_flow(tmp_path: Path, media: Path) -> None:
    from test_watcher import FakeServices

    from whatdvd.indexer import Jackett
    from whatdvd.qbit import QBittorrent
    from whatdvd.web.config import JackettConfig, QbitConfig

    services = FakeServices()
    transport = httpx.MockTransport(services)
    config = ServerConfig(
        roots=(media.resolve(),),
        output_dir=tmp_path / "out",
        token=TOKEN,
        database=tmp_path / "state.db",
        qbit=QbitConfig("http://qb:8080", "admin", "qb-password", category="whatdvd"),
        jackett=JackettConfig("http://jackett", "jackett-secret-key", indexer="rutor"),
    )
    app = create_app(
        config,
        runner=FakeRunner(fake_mktorrent),
        qbit=QBittorrent("http://qb:8080", "admin", "qb-password", transport=transport),
        jackett=Jackett("http://jackett", "jackett-secret-key", indexer="rutor", transport=transport),
        background=False,
    )
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        settings = client.get("/api/config").json()
        assert settings["jackett"]["indexer"] == "rutor" and settings["qbit"]["category"] == "whatdvd"
        assert "qb-password" not in str(settings) and "jackett-secret-key" not in str(settings)

        assert client.post("/api/releases/refresh").json() == {"added": 2}
        data = client.get("/api/releases").json()
        assert data["counts"] == {"new": 2, "active": 0, "finished": 0, "ignored": 0}
        assert data["status"]["jackett"] and data["status"]["qbit"]
        assert "download_url" not in data["releases"][0]
        assert "jackett-secret-key" not in str(data)

        first, second = (r["id"] for r in data["releases"])
        assert client.post(f"/api/releases/{second}/ignore").json()["status"] == "ignored"
        assert client.post(f"/api/releases/{second}/ignore").status_code == 409
        assert client.post(f"/api/releases/{second}/ignore?undo=true").json()["status"] == "new"

        response = client.post(f"/api/releases/{first}/download")
        assert response.status_code == 200 and response.json()["status"] == "sent"
        assert client.post(f"/api/releases/{first}/download").status_code == 409
        assert client.get("/api/releases?group=active").json()["counts"]["active"] == 1

        # 下载完成，路径不在 roots 内：标记失败，不处理
        services.torrent(response.json()["info_hash"], "stalledUP", 1.0, "/somewhere/else")
        assert client.post("/api/releases/sync").status_code == 204
        failed = client.get("/api/releases?group=finished").json()["releases"][0]
        assert failed["status"] == "failed" and "roots" in failed["error"]

        assert client.post("/api/releases/missing/download").status_code == 404
        assert client.post(f"/api/releases/{second}/reprocess").status_code == 409
    assert TestClient(app).get("/api/releases").status_code == 401  # 需要登录


def test_seed_dir_rename_torrent_and_add_to_qbit(tmp_path: Path, media: Path) -> None:
    """发种目录：硬链接改名后做种，再添加到 qB 做种（单独的分类，跳过校验）。"""
    import os

    from test_watcher import FakeServices

    from whatdvd.qbit import QBittorrent
    from whatdvd.web.config import QbitConfig

    services = FakeServices()
    seed_dir = tmp_path / "seed"
    config = ServerConfig(
        roots=(media.resolve(),),
        output_dir=tmp_path / "out",
        token=TOKEN,
        database=tmp_path / "state.db",
        seed_dir=seed_dir,
        qbit=QbitConfig("http://qb:8080", "admin", "pw", path_map=(("/data", str(tmp_path)),)),
    )
    app = create_app(
        config,
        runner=FakeRunner(fake_mktorrent, available=["mktorrent"]),
        qbit=QBittorrent("http://qb:8080", "admin", "pw", transport=httpx.MockTransport(services)),
        background=False,
    )
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        status = client.get("/api/config").json()["seed_dir"]
        assert status == {"path": str(seed_dir), "other_filesystem": [], "error": None}

        body = {"kind": "torrent", "path": str(media / "Movie A"), "piece_length": 24, "seed_name": "The Film (1985)"}
        job = wait_job(client, client.post("/api/jobs", json=body).json()["id"])
        assert job["status"] == "done", job["events"]
        linked = seed_dir / "The Film (1985)"
        assert job["result"]["seed_path"] == str(linked)
        assert os.path.samefile(linked / "VIDEO_TS" / "VTS_01_1.VOB", media / "Movie A" / "VIDEO_TS" / "VTS_01_1.VOB")
        assert (media / "Movie A").is_dir()  # 原始下载不动
        assert job["result"]["torrent_file"] == "The.Film.1985.torrent"
        assert (tmp_path / "out" / "The.Film.1985" / "The.Film.1985.torrent").is_file()

        seeded = client.post(f"/api/jobs/{job['id']}/seed")
        assert seeded.status_code == 200, seeded.text
        assert seeded.json() == {"added": True, "save_path": "/data/seed", "category": "whatdvd-seed"}
        sent = services.added[-1].decode()
        for field in ("whatdvd-seed", "/data/seed", "skip_checking", "Original", "autoTMM"):
            assert field in sent
        assert services.categories[-1] == "category=whatdvd-seed&savePath="

        # ISO 补上扩展名，和同名文件夹不冲突
        body = {"kind": "torrent", "path": str(media / "Movie B.iso"), "piece_length": 24, "seed_name": "The Film (1985)"}
        iso = wait_job(client, client.post("/api/jobs", json=body).json()["id"])
        assert iso["status"] == "done" and iso["result"]["seed_path"] == str(seed_dir / "The Film (1985).iso")

        # 同名但不是同一份数据：任务失败，不覆盖
        body = {"kind": "torrent", "path": str(media / "Plain"), "piece_length": 24, "seed_name": "The Film (1985)"}
        conflict = wait_job(client, client.post("/api/jobs", json=body).json()["id"])
        assert conflict["status"] == "failed" and "不是同一份数据" in conflict["error"]
        assert (linked / "VIDEO_TS" / "VTS_01_1.VOB").exists()

        bad = {"kind": "torrent", "path": str(media / "Movie A"), "piece_length": 24, "seed_name": "a/b"}
        assert client.post("/api/jobs", json=bad).status_code == 400


def test_seed_name_requires_seed_dir(authed: TestClient, media: Path) -> None:
    body = {"kind": "torrent", "path": str(media / "Movie A"), "piece_length": 24, "seed_name": "Film"}
    response = authed.post("/api/jobs", json=body)
    assert response.status_code == 400 and "发种目录" in response.json()["detail"]
    job = wait_job(authed, authed.post("/api/jobs", json={**body, "seed_name": ""}).json()["id"])
    assert job["result"]["seed_path"] == str((media / "Movie A").resolve())  # 没有发种目录时用原始下载
    assert authed.post(f"/api/jobs/{job['id']}/seed").status_code == 404  # 没有配置 qBittorrent


def test_tmdb_lookup(tmp_path: Path, media: Path) -> None:
    """来源页查片名：猜搜索词、搜索、详情里给出 PTP 发种名称和 BHD 标题开头。"""
    from test_tmdb import FakeTmdb

    from whatdvd.tmdb import Tmdb

    fake = FakeTmdb()
    make_file(media / "Иди и смотри (1985) DVD9" / "VIDEO_TS" / "VTS_01_1.VOB", 10)
    config = ServerConfig(roots=(media.resolve(),), output_dir=tmp_path / "out", token=TOKEN, tmdb_api_key="k")
    app = create_app(config, runner=FakeRunner(), tmdb_factory=lambda key: Tmdb(key, transport=httpx.MockTransport(fake)))
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        info = client.get("/api/source", params={"path": str(media / "Иди и смотри (1985) DVD9")}).json()
        assert info["tmdb"] is True and info["disc_kind"] == "DVD5"
        assert info["guess"] == {"query": "Иди и смотри", "year": 1985, "from": "name"}

        results = client.get("/api/tmdb/search", params={"q": "Иди и смотри", "year": 1985}).json()["results"]
        assert results[0]["kind"] == "tv" and results[1]["title"] == "Come and See"

        detail = client.get("/api/tmdb/movie/25237", params={"disc": "DVD9"}).json()
        assert detail["imdb_id"] == "tt0091251"
        assert detail["ptp_name"] == "Come.and.See.1985.DVD9"
        assert detail["bhd_head"] == "Come and See AKA Иди и смотри 1985"

        assert client.post("/api/settings/test/tmdb", json={}).json() == {"ok": True}
        assert client.get("/api/tmdb/search", params={"q": " "}).status_code == 400

        bad = {"kind": "run", "path": str(media / "Movie A"), "count": 3, "title": {"title": "X", "imdb_id": "nope"}}
        assert client.post("/api/jobs", json=bad).status_code == 422


def test_seed_dir_in_settings_page(settings_client: tuple[TestClient, Path], tmp_path: Path) -> None:
    """发种目录可以在设置页面填写，保存后立即生效。"""
    client, _ = settings_client
    assert client.get("/api/settings").json()["values"]["seed_dir"] == ""
    assert client.get("/api/config").json()["seed_dir"] is None
    seed = tmp_path / "seed"
    data = client.put("/api/settings", json={"seed_dir": str(seed)}).json()
    assert data["values"]["seed_dir"] == str(seed) and "seed_dir" in data["overridden"]
    status = client.get("/api/config").json()["seed_dir"]
    assert status["path"] == str(seed) and status["error"] is None and seed.is_dir()
    response = client.put("/api/settings", json={"seed_dir": "relative/seed"})
    assert response.status_code == 400 and "绝对路径" in response.json()["detail"]
    assert client.put("/api/settings", json={"seed_dir": ""}).json()["values"]["seed_dir"] == ""


def test_imdb_dataset_update_and_lookup(tmp_path: Path, media: Path) -> None:
    """设置页面更新 IMDb 数据集；查片名时片名以 IMDb 为准，没有时退回 TMDB 并说明。"""
    from test_imdb_dataset import BASICS, _transport
    from test_tmdb import FakeTmdb

    from whatdvd.imdb_dataset import build
    from whatdvd.tmdb import Tmdb

    without_twin_peaks = [line for line in BASICS if not line.startswith("tt0098936")]

    def builder(path: Path, progress: Any, stop: threading.Event) -> dict[str, Any]:
        return build(path, progress, stop=stop, base_url="https://imdb.test", transport=_transport(without_twin_peaks))

    config = ServerConfig(roots=(media.resolve(),), output_dir=tmp_path / "out", token=TOKEN, tmdb_api_key="k",
                          database=tmp_path / "state" / "whatdvd.db")
    fake = FakeTmdb()
    app = create_app(config, runner=FakeRunner(), imdb_builder=builder,
                     tmdb_factory=lambda key: Tmdb(key, transport=httpx.MockTransport(fake)))
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        status = client.get("/api/imdb").json()
        assert status["info"] is None and status["update"]["running"] is False
        assert status["path"] == str(tmp_path / "state" / "imdb.db")

        detail = client.get("/api/tmdb/movie/25237", params={"disc": "DVD9"}).json()
        assert detail["source"] == "TMDB" and "还没有下载 IMDb 数据集" in detail["notes"][0]

        assert client.post("/api/imdb/update").status_code == 202
        deadline = time.monotonic() + 10
        while client.get("/api/imdb").json()["update"]["running"] and time.monotonic() < deadline:
            time.sleep(0.05)
        status = client.get("/api/imdb").json()
        assert status["update"]["error"] is None and status["info"]["titles"] == 5

        # FakeTmdb 中 25237 的 IMDb 编号是 tt0091251，数据集中有：名字以 IMDb 为准
        detail = client.get("/api/tmdb/movie/25237", params={"disc": "DVD9"}).json()
        assert (detail["source"], detail["title"], detail["original_title"]) == ("IMDb", "Come and See", "Idi i smotri")
        assert detail["ptp_name"] == "Come.and.See.1985.DVD9"
        assert detail["bhd_head"] == "Come and See AKA Idi i smotri 1985"
        assert detail["notes"] == []

        # 搜索结果里直接标出 IMDb 编号和 IMDb 的名字
        found = client.get("/api/tmdb/search", params={"q": "x"}).json()
        assert found["dataset"] is True
        imdb = {r["title"]: r["imdb"] for r in found["results"]}
        assert imdb["Come and See"] == {"id": "tt0091251", "title": "Come and See", "year": 1985}
        assert imdb["Twin Peaks"] == {"id": "tt0098936"}  # 数据集中没有
        assert imdb["The Emerald Forest"] == {"id": None}  # TMDB 中没有 IMDb 编号（模拟的详情取不到）

        # 按片名查时，IMDb 数据集中找到而 TMDB 结果里没有的也列出来
        found = client.get("/api/tmdb/search", params={"q": "Два капитана 2", "year": 1992}).json()
        extra = [r for r in found["results"] if r["kind"] == "imdb"]
        assert [(r["imdb_id"], r["imdb_kind"], r["title"]) for r in extra] == [("tt0183022", "movie", "Dva kapitana II")]
        assert found["notes"] == []

        # 粘贴 IMDb 链接：TMDB 中有的按 IMDb 编号找到
        found = client.get("/api/tmdb/search", params={"q": "https://www.imdb.com/title/tt0091251/"}).json()["results"]
        assert [(r["kind"], r["title"], r["imdb"]["title"]) for r in found] == [("movie", "Come and See", "Come and See")]
        # TMDB 中没有的（例如 Непобедимые）：只用 IMDb 数据集
        found = client.get("/api/tmdb/search", params={"q": "tt0079944"}).json()["results"]
        assert [(r["kind"], r["title"], r["imdb_id"]) for r in found] == [("imdb", "Stalker", "tt0079944")]
        detail = client.get("/api/imdb/title/tt0079944", params={"disc": "DVD5"}).json()
        assert (detail["source"], detail["ptp_name"], detail["url"]) == ("IMDb", "Stalker.1979.DVD5", None)
        assert detail["notes"] == []
        assert client.get("/api/imdb/title/tt0000009").status_code == 404
        assert client.get("/api/tmdb/search", params={"q": "tt0000009"}).json()["results"] == []
        # TMDB 的条目没有 IMDb 编号时，可以用 imdb 参数指定
        detail = client.get("/api/tmdb/movie/25237", params={"imdb": "tt0083658"}).json()
        assert (detail["source"], detail["title"], detail["imdb_id"]) == ("IMDb", "Blade Runner", "tt0083658")

        # 数据集中没有的：退回 TMDB 并说明
        detail = client.get("/api/tmdb/tv/1920").json()
        assert detail["source"] == "TMDB" and "数据集中没有 tt0098936" in detail["notes"][0]


def test_name_search_without_tmdb_key(tmp_path: Path, media: Path) -> None:
    """没有 TMDB API Key 时按片名只查 IMDb 数据集；旧版本数据集提示更新。"""
    import sqlite3

    from test_imdb_dataset import _transport

    from whatdvd.imdb_dataset import build

    db = tmp_path / "state" / "imdb.db"
    build(db, lambda *a: None, base_url="https://imdb.test", transport=_transport())
    config = ServerConfig(roots=(media.resolve(),), output_dir=tmp_path / "out", token=TOKEN,
                          database=tmp_path / "state" / "whatdvd.db")
    app = create_app(config, runner=FakeRunner())
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        found = client.get("/api/tmdb/search", params={"q": "Иди и смотри", "year": 1985}).json()
        assert [(r["kind"], r["imdb_id"], r["title"]) for r in found["results"]] == [("imdb", "tt0091251", "Come and See")]
        detail = client.get("/api/imdb/title/tt0091251", params={"disc": "DVD9"}).json()
        assert detail["ptp_name"] == "Come.and.See.1985.DVD9"

        with sqlite3.connect(db) as conn:  # 模拟旧版本导入的数据集
            conn.execute("DELETE FROM meta WHERE key = 'names_version'")
        assert "旧版本" in client.get("/api/tmdb/search", params={"q": "x"}).json()["notes"][0]

        db.unlink()
        assert client.get("/api/tmdb/search", params={"q": "x"}).status_code == 400


def test_auto_rename_after_download(tmp_path: Path, media: Path) -> None:
    """下载完成的自动处理：有把握时按 IMDb 名建硬链接、带上 BHD 用的片名；没有把握时保留原名并说明。"""
    from test_imdb_dataset import _transport

    from whatdvd.imdb_dataset import build

    build(tmp_path / "state" / "imdb.db", lambda *a: None, base_url="https://imdb.test", transport=_transport())
    make_file(media / "Иди и смотри (1985) DVD9" / "VIDEO_TS" / "VTS_01_1.VOB", 10)
    config = ServerConfig(roots=(media.resolve(),), output_dir=tmp_path / "out", token=TOKEN, seed_dir=tmp_path / "seed",
                          database=tmp_path / "state" / "whatdvd.db", settings_file=tmp_path / "settings.json")
    app = create_app(config, runner=FakeRunner(fake_mktorrent, available=["mktorrent"]), background=False)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        info = client.get("/api/source", params={"path": str(media / "Иди и смотри (1985) DVD9")}).json()
        assert info["suggested"]["imdb_id"] == "tt0091251"  # 来源页预先选中

        def submit(path: Path, hint: str) -> Job:  # 在事件循环中调用（同 qB 同步时）
            return client.portal.call(app.state.submit_auto, path, hint)  # type: ignore[union-attr]

        # 文件夹名就是俄文原名（IMDb 原名 Idi i smotri 的写法）：看得出片名，保持原名，但 BHD 标题照样给出
        job = submit(media / "Иди и смотри (1985) DVD9", "Иди и смотри / Come and See (1985) DVD9 | P")
        assert job.params["seed_name"] == ""
        assert job.params["title"]["imdb_id"] == "tt0091251" and job.params["auto_title"] == "tt0091251"
        assert "自动选中片名：Come and See" in job.params["auto_note"] and "保持原名" in job.params["auto_note"]

        # 文件夹名缩写得看不出片名：改成 IMDb 名
        make_file(media / "IIS_DVD9" / "VIDEO_TS" / "VTS_01_1.VOB", 10)
        job = submit(media / "IIS_DVD9", "Иди и смотри / Come and See (1985) DVD9 | P")
        assert job.params["seed_name"] == "Come.and.See.1985.DVD5"  # 测试用的盘只有 10 字节，按 DVD5 算
        assert "保持原名" not in job.params["auto_note"]
        detail = client.get("/api/imdb/title/tt0091251", params={"folder": "IIS_DVD9"}).json()
        assert detail["folder_ok"] is False
        detail = client.get("/api/imdb/title/tt0091251", params={"folder": "Come.and.See.1985.PAL.DVD9"}).json()
        assert detail["folder_ok"] is True

        job = submit(media / "Movie A", "Совсем другое кино (2001) DVD9")
        assert job.params["seed_name"] == "" and "title" not in job.params
        assert "没有自动选片名" in job.params["auto_note"]

        client.put("/api/settings", json={"auto_rename": False})
        assert client.get("/api/source", params={"path": str(media / "Иди и смотри (1985) DVD9")}).json()["suggested"] is None
        job = submit(media / "Иди и смотри (1985) DVD9", "")
        assert job.params["seed_name"] == "" and "auto_note" not in job.params


def test_imdb_update_error_is_reported(tmp_path: Path, media: Path) -> None:
    from whatdvd.imdb_dataset import DatasetError

    def builder(path: Path, progress: Any, stop: threading.Event) -> dict[str, Any]:
        progress("下载并导入 title.basics（1/2）", 10, 100)
        raise DatasetError("下载 https://datasets.imdbws.com/title.basics.tsv.gz 失败：HTTP 503")

    config = ServerConfig(roots=(media.resolve(),), output_dir=tmp_path / "out", token=TOKEN, database=tmp_path / "w.db")
    app = create_app(config, runner=FakeRunner(), imdb_builder=builder)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        assert client.post("/api/imdb/update").status_code == 202
        deadline = time.monotonic() + 10
        while client.get("/api/imdb").json()["update"]["running"] and time.monotonic() < deadline:
            time.sleep(0.05)
        update = client.get("/api/imdb").json()["update"]
        assert "HTTP 503" in update["error"] and update["phase"] == ""


def test_tmdb_requires_key(authed: TestClient, media: Path) -> None:
    info = authed.get("/api/source", params={"path": str(media / "Movie A")}).json()
    assert info["tmdb"] is False and info["guess"]["query"] == "Movie A"
    response = authed.get("/api/tmdb/search", params={"q": "Film"})
    assert response.status_code == 400 and "TMDB API Key" in response.json()["detail"]


def test_site_names_from_run_result(tmp_path: Path) -> None:
    """截图任务完成后的 BHD 标题：制式、盘型来自识别结果，音轨来自 VOB 的 MediaInfo。"""
    from types import SimpleNamespace

    from whatdvd.web.app import _site_names
    from whatdvd.workflow import DiscResult, RunResult

    mediainfo = tmp_path / "Film.mediainfo.txt"
    mediainfo.write_text("General\nFormat : MPEG-PS\n\nAudio\nFormat : AC-3\nChannel(s) : 6 channels\n"
                         "Channel layout : L R C LFE Ls Rs\n", encoding="utf-8")

    def disc(media_type: str) -> DiscResult:
        analysis = SimpleNamespace(standard="PAL", disc=SimpleNamespace(media_type=media_type))
        return DiscResult(source=tmp_path, label="x", analysis=analysis, output=SimpleNamespace(mediainfo=mediainfo))  # type: ignore[arg-type]

    result = RunResult(discs=[disc("DVD9"), disc("DVD9")])
    params = {"title": {"title": "Come and See", "original_title": "Иди и смотри", "original_language": "ru",
                        "year": 1985, "imdb_id": "tt0091251"}, "region": "RUS", "edition": ""}
    names = _site_names(result, params)
    assert names == {"bhd": "Come and See AKA Иди и смотри 1985 RUS PAL 2xDVD9 MPEG-2 DD5.1", "audio": "DD5.1",
                     "imdb_id": "tt0091251", "tmdb_url": None}
    assert _site_names(result, {}) is None


# ---------- 设置页面 ----------


@pytest.fixture
def settings_client(tmp_path: Path, media: Path) -> Iterator[tuple[TestClient, Path]]:
    from whatdvd.web.config import load_config

    config_file = tmp_path / "config.toml"
    settings_file = tmp_path / "settings.json"
    config_file.write_text(
        f'token = "{TOKEN}"\nroots = ["{media}"]\noutput_dir = "{tmp_path}/out"\n'
        f'database = "{tmp_path}/state.db"\nsettings_file = "{settings_file}"\n\n[screenshots]\ncount = 8\n',
        encoding="utf-8",
    )
    app = create_app(load_config(config_file), runner=FakeRunner(fake_mktorrent), background=False)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client, settings_file


def test_settings_get(settings_client: tuple[TestClient, Path]) -> None:
    client, settings_file = settings_client
    data = client.get("/api/settings").json()
    assert data["values"]["screenshots"]["count"] == 8
    assert data["values"]["qbittorrent"]["url"] == "" and data["values"]["qbittorrent"]["password_set"] is False
    assert data["overridden"] == []
    assert data["fixed"]["settings_file"] == str(settings_file)
    assert data["options"]["aspect_modes"] == ["ua", "minfo", "jietu"]


def test_settings_save_applies_immediately_and_persists(settings_client: tuple[TestClient, Path]) -> None:
    import json
    import stat

    client, settings_file = settings_client
    body = {
        "screenshots": {"count": 5, "dark_filter": False},
        "max_jobs": 2,
        "post": {"template_text": "$name\n$screenshots"},
        "qbittorrent": {"url": "http://qb:8080", "username": "admin", "password": "secret"},
        "jackett": {"url": "http://jackett:9117", "api_key": "key", "interval": 0, "films_only": False},
    }
    data = client.put("/api/settings", json=body).json()
    values = data["values"]
    assert values["screenshots"] == {"count": 5, "aspect": "ua", "dark_filter": False}
    assert values["qbittorrent"]["password_set"] and values["jackett"]["api_key_set"]
    assert values["jackett"]["films_only"] is False and values["rutor"]["films_only"] is True
    assert "secret" not in json.dumps(data) and '"key"' not in json.dumps(data)
    assert "screenshots.count" in data["overridden"] and "qbittorrent.password" in data["overridden"]

    config = client.get("/api/config").json()  # 立即生效
    assert config["screenshot_count"] == 5 and config["max_jobs"] == 2 and config["custom_template"]
    assert config["qbit"]["url"] == "http://qb:8080"
    assert client.get("/api/releases").status_code == 200  # 资源功能随之启用

    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["screenshots"] == {"count": 5, "dark_filter": False}
    assert saved["qbittorrent"]["password"] == "secret"
    assert stat.S_IMODE(settings_file.stat().st_mode) == 0o600

    # 密码为 null 保持不变；其他项为 null 恢复为配置文件的值
    data = client.put("/api/settings", json={"qbittorrent": {"password": None}, "screenshots": {"count": None}}).json()
    assert data["values"]["qbittorrent"]["password_set"] and data["values"]["screenshots"]["count"] == 8
    assert json.loads(settings_file.read_text(encoding="utf-8"))["qbittorrent"]["password"] == "secret"

    # 关掉 qB 和 Jackett
    client.put("/api/settings", json={"qbittorrent": {"url": ""}, "jackett": {"url": ""}})
    assert client.get("/api/releases").status_code == 404


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"screenshots": {"count": 0}}, "正整数"),
        ({"screenshots": {"count": "5"}}, "类型不对"),
        ({"screenshots": {"aspect": "bad"}}, "screenshots.aspect"),
        ({"post": {"template_text": "$name $oops"}}, "模板"),
        ({"max_jobs": 99}, "不能超过"),
        ({"qbittorrent": {"url": "qb:8080"}}, "http://"),
        ({"jackett": {"url": "http://j"}}, "api_key"),
        ({"roots": ["/"]}, "不能在设置页面中修改"),
        ({"token": "x"}, "不能在设置页面中修改"),
        ({"database": "/tmp/x.db"}, "不能在设置页面中修改"),
        ({"screenshots": 5}, "应该是一个表"),
        ({"screenshots": {"nope": 1}}, "不能在设置页面中修改"),
    ],
)
def test_settings_invalid_changes_are_rejected(settings_client: tuple[TestClient, Path], body: dict[str, Any], message: str) -> None:
    client, settings_file = settings_client
    response = client.put("/api/settings", json=body)
    assert response.status_code == 400 and message in response.json()["detail"], response.json()
    assert not settings_file.exists()  # 校验不通过不写入
    assert client.get("/api/config").json()["screenshot_count"] == 8


def test_settings_reset(settings_client: tuple[TestClient, Path]) -> None:
    import json

    client, settings_file = settings_client
    client.put("/api/settings", json={"screenshots": {"count": 3}})
    data = client.delete("/api/settings").json()
    assert data["overridden"] == [] and data["values"]["screenshots"]["count"] == 8
    assert json.loads(settings_file.read_text(encoding="utf-8")) == {}


def test_settings_connection_tests_report_errors(settings_client: tuple[TestClient, Path]) -> None:
    client, _ = settings_client
    response = client.post("/api/settings/test/qbittorrent", json={"url": "http://127.0.0.1:1", "username": "a"})
    assert response.status_code == 400 and "连不上 qBittorrent" in response.json()["detail"]
    response = client.post("/api/settings/test/jackett", json={"url": "http://127.0.0.1:1", "api_key": "k"})
    assert response.status_code == 400 and "连不上 Jackett" in response.json()["detail"]


def test_settings_requires_login(settings_client: tuple[TestClient, Path]) -> None:
    client, _ = settings_client
    client.headers.pop("Authorization")
    assert client.get("/api/settings").status_code == 401
    assert client.put("/api/settings", json={"max_jobs": 2}).status_code == 401


def test_job_limit_can_change_while_running() -> None:
    from whatdvd.web.jobs import JobManager

    async def scenario() -> list[str]:
        manager = JobManager(1)
        release = threading.Event()
        started: list[str] = []

        def worker(job: Job, reporter: Any) -> dict[str, Any]:
            started.append(job.path.name)
            release.wait(5)
            return {"ok": True}

        for name in ("a", "b"):
            manager.submit(Job("run", Path(name), {}, Path("/tmp")), worker)
        await asyncio.sleep(0.2)
        assert started == ["a"]  # 只能同时运行 1 个
        await manager.set_limit(2)
        await asyncio.sleep(0.2)
        assert sorted(started) == ["a", "b"]  # 调大后排队的任务立即开始
        release.set()
        await manager.wait_all()
        return started

    asyncio.run(scenario())


def test_release_filters_and_paging(tmp_path: Path, media: Path) -> None:
    from whatdvd.store import Record

    from whatdvd.indexer import Jackett
    from whatdvd.web.config import JackettConfig

    config = ServerConfig(
        roots=(media.resolve(),), output_dir=tmp_path / "out", token=TOKEN, database=tmp_path / "state.db",
        jackett=JackettConfig("http://jackett", "k"),
    )

    jackett = Jackett("http://jackett", "k", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    app = create_app(config, runner=FakeRunner(fake_mktorrent), jackett=jackett, background=False)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        store = app.state.watcher.store
        rows = [
            ("a", "Alpha / Альфа (1999) DVD9", "DVD9", 1, 3, []),
            ("b", "Beta (2001) DVD5 | P", "DVD5", 1, 0, ["带俄语配音标记（P）"]),
            ("c", "Gamma (2001) 2 DVD9", "2×DVD9", 2, 5, []),
        ]
        for i, (rid, title, kind, discs, seeders, warnings) in enumerate(rows):
            store.save(Record(id=rid, title=title, source="RuTor", kind=kind, discs=discs, seeders=seeders,
                              warnings=warnings, published=float(i)))

        def ids(query: str = "") -> list[str]:
            return [r["id"] for r in client.get(f"/api/releases?{query}").json()["releases"]]

        assert ids() == ["c", "b", "a"]
        assert ids("q=альфа") == ["a"]  # 不区分大小写，支持西里尔字母
        assert ids("q=2001 beta") == ["b"]  # 多个词都要包含
        assert ids("kind=DVD9") == ["a"]
        assert ids("kind=multi") == ["c"]
        assert ids("seeded=true") == ["c", "a"]
        assert ids("clean=true") == ["c", "a"]
        assert ids("limit=2") == ["c", "b"] and ids("limit=2&offset=2") == ["a"]
        data = client.get("/api/releases?kind=DVD5").json()
        assert data["total"] == 1 and data["counts"]["new"] == 3
        assert data["status"]["backfill"] == {"running": False, "done": 0, "total": 0, "added": 0, "last": None}
        assert client.get("/api/releases?limit=500").status_code == 422

        # 本地 IMDb 数据集中的匹配：没有数据集时为 None；唯一一个年份对得上的才算找到
        assert data["releases"][0]["imdb"] is None
        from test_imdb_dataset import _transport

        from whatdvd.imdb_dataset import build

        build(tmp_path / "imdb.db", lambda *a: None, base_url="https://imdb.test", transport=_transport())
        store.save(Record(id="d", title="Иди и смотри / Come and See (1985) DVD9", source="RuTor", kind="DVD9"))
        store.save(Record(id="e", title="Неизвестный фильм (2003) DVD5", source="RuTor", kind="DVD5"))
        found = client.get("/api/releases?q=1985").json()["releases"][0]["imdb"]
        assert (found["id"], found["title"], found["year"]) == ("tt0091251", "Come and See", 1985)
        missing = client.get("/api/releases?q=Неизвестный").json()["releases"][0]["imdb"]
        assert missing["id"] is None and "没有" in missing["reason"]

        response = client.post("/api/releases/backfill")
        assert response.status_code == 202 and response.json()["total"] > 100


def test_settings_enable_rutor(settings_client: tuple[TestClient, Path]) -> None:
    client, _ = settings_client
    assert client.get("/api/releases").status_code == 404
    data = client.put("/api/settings", json={"rutor": {"url": "https://rutor.info", "interval": 0}}).json()
    assert data["values"]["rutor"]["url"] == "https://rutor.info"
    assert client.get("/api/config").json()["rutor"]["url"] == "https://rutor.info"
    status = client.get("/api/releases").json()["status"]
    assert status["rutor"] is True and status["jackett"] is False
    response = client.post("/api/settings/test/rutor", json={"url": "ftp://x"})
    assert response.status_code == 400
    response = client.post("/api/settings/test/rutor", json={"url": "http://127.0.0.1:1"})
    assert response.status_code == 400 and "连不上 rutor" in response.json()["detail"]
