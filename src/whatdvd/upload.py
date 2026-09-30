"""图床上传。上传器是可插拔接口，目前实现 Pixhost（做法参考 minfo）。"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

PIXHOST_DOMAINS = ("pixhost.to", "pixhost.cc")

_THUMB_HOST = re.compile(r"^t(\d+)\.pixhost\.(to|cc)$")


class UploadError(RuntimeError):
    pass


@dataclass(frozen=True)
class UploadedImage:
    direct_url: str
    """原图直链，写进发布说明。"""
    thumbnail_url: str
    page_url: str


class ImageHost(Protocol):
    name: str

    def upload(self, path: Path) -> UploadedImage: ...


def pixhost_direct_url(thumbnail_url: str) -> str:
    """把缩略图地址改写成原图直链：/thumbs/ → /images/，tN.pixhost.to → imgN.pixhost.to。"""
    parts = urlsplit(thumbnail_url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UploadError(f"Pixhost 返回的缩略图地址无效：{thumbnail_url!r}")
    host = parts.hostname.lower()
    if match := _THUMB_HOST.match(host):
        host = f"img{match[1]}.pixhost.{match[2]}"
    netloc = f"{host}:{parts.port}" if parts.port else host
    path = parts.path.replace("/thumbs/", "/images/", 1)
    return urlunsplit((parts.scheme, netloc, path, parts.query, parts.fragment))


class Pixhost:
    name = "Pixhost"

    def __init__(
        self,
        domain: str = "pixhost.to",
        *,
        proxy: str | None = None,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if domain not in PIXHOST_DOMAINS:
            raise ValueError(f"Pixhost 域名只能是 {' 或 '.join(PIXHOST_DOMAINS)}：{domain}")
        self.endpoint = f"https://api.{domain}/images"
        self._client = httpx.Client(proxy=proxy, timeout=timeout, transport=transport)

    def upload(self, path: Path) -> UploadedImage:
        try:
            with path.open("rb") as image:
                response = self._client.post(
                    self.endpoint,
                    files={"img": (path.name, image, "image/png")},
                    data={"content_type": "0", "max_th_size": "420"},
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as error:
            raise UploadError(f"连接 Pixhost 失败：{error}") from error
        if not response.is_success:
            raise UploadError(f"Pixhost 返回 HTTP {response.status_code}")
        try:
            payload = response.json()
            page_url, thumbnail_url = payload["show_url"], payload["th_url"]
        except (ValueError, KeyError, TypeError):
            raise UploadError(f"Pixhost 返回的内容无法识别：{response.text[:200]!r}") from None
        return UploadedImage(
            direct_url=pixhost_direct_url(thumbnail_url),
            thumbnail_url=thumbnail_url,
            page_url=page_url,
        )

    def close(self) -> None:
        self._client.close()


@dataclass(frozen=True)
class UploadResult:
    path: Path
    image: UploadedImage | None
    error: str | None = None


def upload_all(
    host: ImageHost,
    paths: Sequence[Path],
    progress: Callable[[str], None] = lambda _: None,
) -> list[UploadResult]:
    """逐张上传，单张失败不影响其余。"""
    results: list[UploadResult] = []
    for path in paths:
        try:
            image = host.upload(path)
        except UploadError as error:
            progress(f"{path.name} 上传失败：{error}")
            results.append(UploadResult(path=path, image=None, error=str(error)))
            continue
        progress(f"{path.name} 上传完成：{image.direct_url}")
        results.append(UploadResult(path=path, image=image))
    return results
