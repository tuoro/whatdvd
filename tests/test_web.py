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
    assert job["params"] == {"announces": ["https://t.example/a"], "piece_length": 22}
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
