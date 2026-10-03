"""qBittorrent Web API（v2）客户端：只对接 API，不负责 qB 的部署。"""

from __future__ import annotations

from typing import Any

import base64
import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlsplit

import httpx

# 下载完成（做种中、暂停做种、排队做种、校验已完成的数据……）
COMPLETED_STATES = frozenset(
    {"uploading", "stalledUP", "pausedUP", "stoppedUP", "queuedUP", "forcedUP", "checkingUP"}
)


class QbitError(RuntimeError):
    pass


@dataclass(frozen=True)
class TorrentInfo:
    hash: str
    name: str
    state: str
    progress: float
    content_path: str
    """qB 中的路径：多文件种子为顶层目录，单文件种子为文件本身。"""
    size: int

    @property
    def completed(self) -> bool:
        return self.progress >= 1 and self.state in COMPLETED_STATES


class QBittorrent:
    def __init__(
        self,
        url: str,
        username: str = "",
        password: str = "",
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base = url.rstrip("/")
        self._username = username
        self._password = password
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._logged_in = False

    def close(self) -> None:
        self._client.close()

    def login(self) -> None:
        try:
            response = self._client.post(
                f"{self._base}/api/v2/auth/login",
                data={"username": self._username, "password": self._password},
            )
        except httpx.HTTPError as error:
            raise QbitError(f"连不上 qBittorrent：{error or type(error).__name__}") from error
        if response.status_code == 403:
            raise QbitError("qBittorrent 拒绝登录：失败次数过多，IP 被暂时封禁")
        # qB 5.x：成功 204、失败 401；4.x：成功 200 "Ok."、失败 200 "Fails."
        if response.status_code == 401 or response.text.strip() == "Fails.":
            raise QbitError("qBittorrent 登录失败：用户名或密码不正确")
        if response.status_code not in (200, 204):
            raise QbitError(f"qBittorrent 登录失败：HTTP {response.status_code}")
        self._logged_in = True

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> httpx.Response:
        if not self._logged_in:
            self.login()
        url = f"{self._base}/api/v2/{path}"
        try:
            response = self._client.request(method, url, params=params, data=data, files=files)
            if response.status_code == 403:  # 会话过期，重新登录一次
                self.login()
                response = self._client.request(method, url, params=params, data=data, files=files)
        except httpx.HTTPError as error:
            raise QbitError(f"连不上 qBittorrent：{error or type(error).__name__}") from error
        return response

    def version(self) -> str:
        response = self._request("GET", "app/version")
        if not response.is_success:
            raise QbitError(f"qBittorrent 返回 HTTP {response.status_code}")
        return response.text.strip()

    def ensure_category(self, category: str, save_path: str | None = None) -> None:
        data = {"category": category, "savePath": save_path or ""}
        response = self._request("POST", "torrents/createCategory", data=data)
        if response.status_code not in (200, 409):  # 409：分类已存在
            raise QbitError(f"创建分类失败：HTTP {response.status_code}")

    def add(
        self,
        *,
        torrent: bytes | None = None,
        magnet: str | None = None,
        category: str,
        save_path: str | None = None,
        tags: Sequence[str] = (),
        seeding: bool = False,
    ) -> bool:
        """返回 False 表示 qB 中已有这个种子（5.x 返回 409）。

        seeding：数据已经在 save_path 中（我们自己做的种子），跳过校验直接做种；按种子原样的目录结构，
        不让自动管理模式改掉保存路径。"""
        if (torrent is None) == (magnet is None):
            raise ValueError("torrent 和 magnet 必须且只能给一个")
        data = {"category": category}
        if save_path:
            data["savepath"] = save_path
        if tags:
            data["tags"] = ",".join(tags)
        if seeding:
            data |= {"skip_checking": "true", "contentLayout": "Original", "autoTMM": "false"}
        files = None
        if torrent is not None:
            files = {"torrents": ("whatdvd.torrent", torrent, "application/x-bittorrent")}
        else:
            data["urls"] = magnet or ""
        response = self._request("POST", "torrents/add", data=data, files=files)
        if response.status_code == 409:
            return False
        if response.status_code == 415:
            raise QbitError("qBittorrent 认为种子文件无效")
        if not response.is_success or response.text.strip() == "Fails.":
            raise QbitError(f"qBittorrent 拒绝添加种子（HTTP {response.status_code}：{response.text.strip()[:100]}）")
        return True

    def torrents(self, *, category: str | None = None, hashes: Sequence[str] = ()) -> list[TorrentInfo]:
        params: dict[str, str] = {}
        if category is not None:
            params["category"] = category
        if hashes:
            params["hashes"] = "|".join(hashes)
        response = self._request("GET", "torrents/info", params=params)
        if not response.is_success:
            raise QbitError(f"读取种子列表失败：HTTP {response.status_code}")
        try:
            return [
                TorrentInfo(
                    hash=str(item["hash"]).lower(),
                    name=str(item["name"]),
                    state=str(item["state"]),
                    progress=float(item["progress"]),
                    content_path=str(item.get("content_path") or ""),
                    size=int(item.get("size") or 0),
                )
                for item in response.json()
            ]
        except (ValueError, KeyError, TypeError):
            raise QbitError("qBittorrent 返回的种子列表无法识别") from None


# ---------- 种子 hash ----------


def _bencode_end(data: bytes, index: int) -> int:
    """返回从 index 开始的 bencode 值结束后的位置。"""
    char = data[index : index + 1]
    if char == b"i":
        return data.index(b"e", index) + 1
    if char in (b"l", b"d"):
        index += 1
        while data[index : index + 1] != b"e":
            index = _bencode_end(data, index)
        return index + 1
    if char.isdigit():
        colon = data.index(b":", index)
        return colon + 1 + int(data[index:colon])
    raise ValueError(f"无效的 bencode（位置 {index}）")


def _bdecode(data: bytes, index: int) -> tuple[Any, int]:
    char = data[index : index + 1]
    if char == b"i":
        end = data.index(b"e", index)
        return int(data[index + 1 : end]), end + 1
    if char == b"l":
        items, index = [], index + 1
        while data[index : index + 1] != b"e":
            item, index = _bdecode(data, index)
            items.append(item)
        return items, index + 1
    if char == b"d":
        mapping, index = {}, index + 1
        while data[index : index + 1] != b"e":
            key, index = _bdecode(data, index)
            mapping[key], index = _bdecode(data, index)
        return mapping, index + 1
    if char.isdigit():
        colon = data.index(b":", index)
        end = colon + 1 + int(data[index:colon])
        return data[colon + 1 : end], end
    raise ValueError(f"无效的 bencode（位置 {index}）")


def torrent_files(data: bytes) -> tuple[str, list[tuple[str, int]]]:
    """种子里的名字（单文件为文件名，多文件为最外层文件夹名）和文件列表（相对路径，大小）。"""
    try:
        meta, _ = _bdecode(data, 0)
        info = meta[b"info"]
        name = info[b"name"].decode("utf-8", "replace")
        if b"files" not in info:
            return name, [(name, int(info.get(b"length", 0)))]
        files = [
            ("/".join(part.decode("utf-8", "replace") for part in item[b"path"]), int(item.get(b"length", 0)))
            for item in info[b"files"]
        ]
    except (ValueError, IndexError, KeyError, TypeError, AttributeError) as error:
        raise QbitError(f"种子文件无效：{error}") from None
    return name, files


def torrent_contents(data: bytes) -> tuple[str, list[str]]:
    """种子里的名字和文件列表（相对路径）。"""
    name, files = torrent_files(data)
    return name, [path for path, _ in files]


def torrent_info_hash(data: bytes) -> str:
    """种子文件的 info hash（v1，40 位十六进制小写）。"""
    try:
        if data[:1] != b"d":
            raise ValueError("不是字典")
        index = 1
        while data[index : index + 1] != b"e":
            key_end = _bencode_end(data, index)
            key = data[data.index(b":", index) + 1 : key_end]
            value_end = _bencode_end(data, key_end)
            if key == b"info":
                return hashlib.sha1(data[key_end:value_end]).hexdigest()
            index = value_end
    except (ValueError, IndexError) as error:
        raise QbitError(f"种子文件无效：{error}") from None
    raise QbitError("种子文件中没有 info")


def magnet_info_hash(magnet: str) -> str | None:
    """磁力链接中的 btih（支持 40 位十六进制和 32 位 base32），返回十六进制小写。"""
    for xt in parse_qs(urlsplit(magnet).query).get("xt", []):
        match = re.fullmatch(r"urn:btih:([0-9a-fA-F]{40}|[A-Za-z2-7]{32})", xt)
        if match:
            value = match[1]
            return value.lower() if len(value) == 40 else base64.b32decode(value.upper()).hex()
    return None


# ---------- 路径映射 ----------


@dataclass(frozen=True)
class PathMap:
    """qB 中的路径 → whatdvd 看到的路径（两边挂载点不同时使用）。最长前缀优先。"""

    pairs: tuple[tuple[str, str], ...] = ()

    def to_local(self, remote: str) -> Path:
        path = PurePosixPath(remote)
        for prefix, local in sorted(self.pairs, key=lambda pair: len(pair[0]), reverse=True):
            base = PurePosixPath(prefix)
            if path == base or base in path.parents:
                return Path(local) / path.relative_to(base)
        return Path(remote)

    def to_remote(self, local: Path) -> str:
        """whatdvd 看到的路径 → qB 中的路径。"""
        path = PurePosixPath(local.as_posix())
        for remote, prefix in sorted(self.pairs, key=lambda pair: len(pair[1]), reverse=True):
            base = PurePosixPath(prefix)
            if path == base or base in path.parents:
                return str(PurePosixPath(remote) / path.relative_to(base))
        return str(path)
