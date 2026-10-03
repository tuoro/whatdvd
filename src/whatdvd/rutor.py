"""直接读取 rutor 的搜索页（公开站，不需要登录）。

Jackett 每个关键词只读第 1 页（100 条）；rutor 本身每个关键词可以翻到第 20 页（2000 条）。
搜索地址：/search/<页码>/0/100/0/<关键词>，按发布时间从新到旧，每页 100 条。
"""

from __future__ import annotations

import datetime
import html
import re
import time
from collections.abc import Callable
from urllib.parse import quote

import httpx

from .indexer import IndexerError, Release

SOURCE = "rutor 直连"

NON_FILM = (2, 3, 13)
"""rutor 中有 DVD 原盘的非影视分类：音乐、其他（讲座、教程）、体育和健康。"""
PAGE_SIZE = 100
MAX_PAGES = 20
"""rutor 每个关键词最多给出 2000 条。"""

_MONTHS = {"Янв": 1, "Фев": 2, "Мар": 3, "Апр": 4, "Май": 5, "Июн": 6,
           "Июл": 7, "Авг": 8, "Сен": 9, "Окт": 10, "Ноя": 11, "Дек": 12}
_UNITS = {"B": 1, "kB": 1024, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}

_TOTAL = re.compile(r"Результатов поиска\s*(\d+)")
_ROW = re.compile(r'<tr class="(?:gai|tum)">(.*?)</tr>', re.S)
_DATE = re.compile(r"^\s*<td>(\d{1,2})&nbsp;(\w+)&nbsp;(\d{2})</td>")
_DOWNLOAD = re.compile(r'class="downgif" href="([^"]+)"')
_MAGNET = re.compile(r'href="(magnet:\?xt=urn:btih:([0-9a-fA-F]{40})[^"]*)"')
_TITLE = re.compile(r'<a href="(/torrent/(\d+)/[^"]*)">(.*?)</a>', re.S)
_SIZE = re.compile(r'<td align="right">([\d.]+)&nbsp;(\w+)</td>')
_SEEDERS = re.compile(r'<span class="green">.*?&nbsp;(\d+)</span>', re.S)


class RutorFormatError(IndexerError):
    """页面结构和预期不符（rutor 改版）。"""


def _published(day: str, month: str, year: str) -> float | None:
    number = _MONTHS.get(month)
    if number is None:
        return None
    try:
        date = datetime.datetime(2000 + int(year), number, int(day), tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    return date.timestamp()


def parse_search(text: str, base_url: str) -> tuple[int, list[Release]]:
    """返回（搜索结果总数，这一页的资源）。找不到总数说明页面格式变了。"""
    total = _TOTAL.search(text)
    if total is None:
        raise RutorFormatError("rutor 页面里没有找到搜索结果，可能是网站改版或地址不对")
    releases = []
    for row in _ROW.findall(text):
        title = _TITLE.search(row)
        magnet = _MAGNET.search(row)
        if title is None or magnet is None:
            raise RutorFormatError("rutor 搜索结果的格式变了，无法读取标题或磁力链接")
        download = _DOWNLOAD.search(row)
        size = _SIZE.search(row)
        seeders = _SEEDERS.search(row)
        date = _DATE.search(row)
        path, torrent_id, name = title.groups()
        download_url = download.group(1) if download else None
        if download_url and download_url.startswith("//"):
            download_url = "https:" + download_url
        releases.append(
            Release(
                guid=f"{base_url}/torrent/{torrent_id}",
                indexer=SOURCE,
                title=html.unescape(re.sub(r"\s+", " ", name)).strip(),
                size=int(float(size.group(1)) * _UNITS.get(size.group(2), 1)) if size else 0,
                published=_published(*date.groups()) if date else None,
                details_url=base_url + path,
                download_url=download_url,
                magnet=html.unescape(magnet.group(1)),
                info_hash=magnet.group(2).lower(),
                seeders=int(seeders.group(1)) if seeders else None,
            )
        )
    return int(total.group(1)), releases


class Rutor:
    def __init__(
        self,
        url: str = "https://rutor.info",
        *,
        delay: float = 0.7,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """delay：两次请求之间的间隔（秒），避免给网站造成压力。"""
        self.base_url = url.rstrip("/")
        self._delay = delay
        self._last = 0.0
        self._client = httpx.Client(
            timeout=timeout, transport=transport, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 whatdvd"}
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, url: str) -> httpx.Response:
        wait = self._last + self._delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            response = self._client.get(url)
        except httpx.HTTPError as error:
            raise IndexerError(f"连不上 rutor：{error or type(error).__name__}") from error
        finally:
            self._last = time.monotonic()
        return response

    def search(self, query: str, page: int = 0, category: int = 0) -> tuple[int, list[Release]]:
        """一页搜索结果：（总数，资源）。category 为 rutor 的分类，0 为全部。"""
        response = self._get(f"{self.base_url}/search/{page}/{category}/100/0/{quote(query)}")
        if not response.is_success:
            raise IndexerError(f"rutor 返回 HTTP {response.status_code}")
        return parse_search(response.text, self.base_url)

    def search_all(
        self, query: str, progress: Callable[[int, int], None] | None = None, category: int = 0
    ) -> list[Release]:
        """翻完所有页（最多 20 页）。progress(已读页数, 总页数)。"""
        total, releases = self.search(query, 0, category)
        pages = min((total + PAGE_SIZE - 1) // PAGE_SIZE, MAX_PAGES)
        if progress is not None:
            progress(1, max(pages, 1))
        for page in range(1, pages):
            releases += self.search(query, page, category)[1]
            if progress is not None:
                progress(page + 1, pages)
        return releases

    def films(self, query: str, all_pages: bool = False) -> list[Release]:
        """只要影视类：全部分类的结果去掉 NON_FILM 分类中的。rutor 搜索一次只能选一个分类，
        而影视分类有十个，所以反过来搜非影视的几个分类再剔除。同样按时间倒序读同样多的页，
        全部结果里出现的非影视资源一定也在对应分类的结果里。"""
        def read(category: int) -> list[Release]:
            return self.search_all(query, category=category) if all_pages else self.search(query, 0, category)[1]

        releases = read(0)
        if not releases:
            return releases
        other = {r.guid for category in NON_FILM for r in read(category)}
        return [r for r in releases if r.guid not in other]

    def page(self, url: str) -> str:
        """发布页（检查描述里的“发布类型 / 画质”）。"""
        response = self._get(url)
        if not response.is_success:
            raise IndexerError(f"rutor 发布页返回 HTTP {response.status_code}")
        return response.text

    def fetch(self, download_url: str) -> bytes:
        """下载种子文件（d.rutor.info，不需要登录）。"""
        response = self._get(download_url)
        if not response.is_success or not response.content.startswith(b"d"):
            raise IndexerError(f"从 rutor 下载种子失败：HTTP {response.status_code}")
        return response.content
