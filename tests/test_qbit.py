import base64
from pathlib import Path

import httpx
import pytest

from whatdvd.qbit import PathMap, QBittorrent, QbitError, TorrentInfo, magnet_info_hash, torrent_info_hash

# 最小的单文件种子（a.txt，6 字节）
TORRENT = (
    b"d10:created by13:mktorrent 1.113:creation datei1700000000e4:infod6:lengthi6e4:name5:a.txt"
    b"12:piece lengthi262144e6:pieces20:" + bytes(range(20)) + b"7:privatei1eee"
)


class FakeQbit:
    """模拟 qBittorrent Web API。version=5：登录成功 204、失败 401；version=4：200 "Ok." / "Fails."。"""

    def __init__(self, version: int = 5) -> None:
        self.version = version
        self.sid = "abc"
        self.requests: list[httpx.Request] = []
        self.torrents: list[dict[str, object]] = []
        self.added: list[dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/api/v2/auth/login":
            form = dict(x.split("=") for x in request.content.decode().split("&"))
            ok = form == {"username": "admin", "password": "secret"}
            if self.version == 5:
                if not ok:
                    return httpx.Response(401)
                return httpx.Response(204, headers={"set-cookie": f"QBT_SID_8080={self.sid}; path=/"})
            return httpx.Response(200, text="Ok." if ok else "Fails.", headers={"set-cookie": f"SID={self.sid}; path=/"} if ok else {})
        if self.sid not in request.headers.get("cookie", ""):
            return httpx.Response(403, text="Forbidden")
        if path == "/api/v2/app/version":
            return httpx.Response(200, text=f"v{self.version}.0.0")
        if path == "/api/v2/torrents/createCategory":
            return httpx.Response(409 if self.version == 5 and self.added else 200)
        if path == "/api/v2/torrents/add":
            body = request.content
            if b"dup" in body:  # 已在 qB 中
                return httpx.Response(409, text="Conflict")
            self.added.append({"body": body.decode("latin-1")})
            return httpx.Response(200, text="Ok.")
        if path == "/api/v2/torrents/info":
            return httpx.Response(200, json=self.torrents)
        return httpx.Response(404)


def _client(fake: FakeQbit, password: str = "secret") -> QBittorrent:
    return QBittorrent("http://qb:8080/", "admin", password, transport=httpx.MockTransport(fake))


@pytest.mark.parametrize("version", [4, 5])
def test_login_and_version(version: int) -> None:
    fake = FakeQbit(version)
    assert _client(fake).version() == f"v{version}.0.0"


@pytest.mark.parametrize("version", [4, 5])
def test_wrong_password(version: int) -> None:
    with pytest.raises(QbitError, match="用户名或密码不正确"):
        _client(FakeQbit(version), "wrong").version()


def test_banned_ip() -> None:
    client = QBittorrent("http://qb", transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    with pytest.raises(QbitError, match="封禁"):
        client.login()


def test_unreachable() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(QbitError, match="连不上 qBittorrent"):
        QBittorrent("http://qb", transport=httpx.MockTransport(fail)).version()


def test_relogin_when_session_expires() -> None:
    fake = FakeQbit()
    client = _client(fake)
    client.version()
    fake.sid = "new"  # qB 重启，旧会话失效
    assert client.version() == "v5.0.0"
    assert [r.url.path for r in fake.requests].count("/api/v2/auth/login") == 2


def test_add_torrent_file_with_category_and_tags() -> None:
    fake = FakeQbit()
    assert _client(fake).add(torrent=TORRENT, category="whatdvd", save_path="/downloads/dvd", tags=["whatdvd"])
    body = fake.added[0]["body"]
    assert 'name="torrents"; filename="whatdvd.torrent"' in body
    assert 'name="category"\r\n\r\nwhatdvd' in body
    assert 'name="savepath"\r\n\r\n/downloads/dvd' in body
    assert 'name="tags"\r\n\r\nwhatdvd' in body


def test_add_magnet() -> None:
    fake = FakeQbit()
    _client(fake).add(magnet="magnet:?xt=urn:btih:" + "a" * 40, category="whatdvd")
    from urllib.parse import parse_qs

    assert parse_qs(fake.added[0]["body"])["urls"] == ["magnet:?xt=urn:btih:" + "a" * 40]


def test_add_existing_torrent_returns_false() -> None:
    fake = FakeQbit()
    assert _client(fake).add(torrent=TORRENT + b"dup", category="whatdvd") is False


def test_add_requires_exactly_one_source() -> None:
    with pytest.raises(ValueError):
        _client(FakeQbit()).add(category="x")


def test_torrents() -> None:
    fake = FakeQbit()
    fake.torrents = [
        {"hash": "ABC", "name": "Movie", "state": "stalledUP", "progress": 1, "content_path": "/dl/Movie", "size": 5},
        {"hash": "def", "name": "Other", "state": "downloading", "progress": 0.5, "content_path": "/dl/Other"},
        {"hash": "123", "name": "Paused", "state": "stoppedUP", "progress": 1.0, "content_path": "/dl/P"},
    ]
    infos = _client(fake).torrents(category="whatdvd", hashes=["abc", "def"])
    assert infos[0] == TorrentInfo("abc", "Movie", "stalledUP", 1.0, "/dl/Movie", 5)
    assert [i.completed for i in infos] == [True, False, True]
    query = fake.requests[-1].url.params
    assert query["category"] == "whatdvd" and query["hashes"] == "abc|def"


def test_checking_after_completion_is_not_completed() -> None:
    assert not TorrentInfo("a", "n", "checkingDL", 1.0, "/x", 0).completed
    assert TorrentInfo("a", "n", "checkingUP", 1.0, "/x", 0).completed


def test_torrent_info_hash() -> None:
    import hashlib

    start = TORRENT.index(b"4:info") + 6
    assert torrent_info_hash(TORRENT) == hashlib.sha1(TORRENT[start:-1]).hexdigest()
    with pytest.raises(QbitError):
        torrent_info_hash(b"not a torrent")
    with pytest.raises(QbitError, match="没有 info"):
        torrent_info_hash(b"d3:fooi1ee")


def test_magnet_info_hash() -> None:
    hex_hash = "0123456789abcdef0123456789abcdef01234567"
    assert magnet_info_hash(f"magnet:?xt=urn:btih:{hex_hash.upper()}&dn=x") == hex_hash
    b32 = base64.b32encode(bytes.fromhex(hex_hash)).decode()
    assert magnet_info_hash(f"magnet:?dn=x&xt=urn:btih:{b32}") == hex_hash
    assert magnet_info_hash("magnet:?dn=x") is None


@pytest.mark.parametrize(
    ("remote", "local"),
    [
        ("/downloads/Movie (2001)", "/media/qb/Movie (2001)"),
        ("/downloads/dvd/Movie", "/srv/dvd/Movie"),  # 更长的前缀优先
        ("/downloads", "/media/qb"),
        ("/downloadsX/Movie", "/downloadsX/Movie"),  # 只按完整的目录名匹配
        ("/other/Movie", "/other/Movie"),
    ],
)
def test_path_map(remote: str, local: str) -> None:
    mapping = PathMap((("/downloads", "/media/qb"), ("/downloads/dvd", "/srv/dvd")))
    assert mapping.to_local(remote) == Path(local)


def test_path_map_to_remote() -> None:
    mapping = PathMap((("/downloads", "/media/qb"), ("/seed", "/media/qb/seed")))
    assert mapping.to_remote(Path("/media/qb/seed/Film")) == "/seed/Film"  # 最长前缀优先
    assert mapping.to_remote(Path("/media/qb/Film")) == "/downloads/Film"
    assert mapping.to_remote(Path("/other/Film")) == "/other/Film"
