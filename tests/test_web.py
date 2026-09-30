from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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
