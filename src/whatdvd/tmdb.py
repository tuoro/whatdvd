"""TMDB（themoviedb.org）查片名：英文名、原名、年份和对应的 IMDb 编号。

PTP 要求文件夹名和 IMDb 的原名或英文名一致，BHD 的标题用 IMDb 或 TMDB 的官方英文名。
API Key 在 themoviedb.org 的账号设置中免费申请；v3 的 API Key 和 v4 的读取令牌都可以用。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

import httpx

API = "https://api.themoviedb.org/3"
Kind = Literal["movie", "tv"]


class TmdbError(RuntimeError):
    pass


@dataclass(frozen=True)
class Match:
    kind: Kind
    id: int
    title: str
    """英文名（language=en-US）。"""
    original_title: str
    original_language: str
    year: int | None
    imdb_id: str | None = None
    """搜索结果中没有，取详情时才有。"""

    @property
    def url(self) -> str:
        return f"https://www.themoviedb.org/{self.kind}/{self.id}"

    def public(self) -> dict[str, Any]:
        imdb = f"https://www.imdb.com/title/{self.imdb_id}/" if self.imdb_id else None
        return {**asdict(self), "url": self.url, "imdb_url": imdb}


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


def _match(kind: Kind, item: dict[str, Any]) -> Match:
    title_key, original_key, date_key = (
        ("title", "original_title", "release_date") if kind == "movie" else ("name", "original_name", "first_air_date")
    )
    imdb = (item.get("external_ids") or {}).get("imdb_id") or item.get("imdb_id")
    return Match(
        kind=kind,
        id=int(item["id"]),
        title=str(item.get(title_key) or item.get(original_key) or ""),
        original_title=str(item.get(original_key) or ""),
        original_language=str(item.get("original_language") or ""),
        year=_year(item.get(date_key)),
        imdb_id=imdb or None,
    )


class Tmdb:
    def __init__(self, api_key: str, *, timeout: float = 20.0, transport: httpx.BaseTransport | None = None) -> None:
        # v4 的读取令牌是 JWT（以 eyJ 开头），放在 Authorization 头；v3 的 API Key 放在查询参数里
        self._params: dict[str, str] = {"language": "en-US"}
        headers = {"Accept": "application/json"}
        if api_key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            self._params["api_key"] = api_key
        self._client = httpx.Client(base_url=API, timeout=timeout, headers=headers, transport=transport)

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, **params: str) -> dict[str, Any]:
        try:
            response = self._client.get(path, params={**self._params, **params})
        except httpx.HTTPError as error:
            raise TmdbError(f"连不上 TMDB：{error or type(error).__name__}") from error
        if response.status_code == 401:
            raise TmdbError("TMDB API Key 无效")
        if response.status_code == 404:
            raise TmdbError("TMDB 中没有这个条目")
        if not response.is_success:
            raise TmdbError(f"TMDB 返回 HTTP {response.status_code}")
        try:
            data: dict[str, Any] = response.json()
        except ValueError:
            raise TmdbError("TMDB 返回的内容无法解析") from None
        return data

    def search(self, query: str, year: int | None = None, limit: int = 10) -> list[Match]:
        """电影和剧集一起搜，按 TMDB 的热度排序。搜索也匹配各语言的译名（例如俄文片名）。"""
        found: list[tuple[float, Match]] = []
        for kind, year_param in (("movie", "year"), ("tv", "first_air_date_year")):
            params = {"query": query, "include_adult": "false"}
            if year:
                params[year_param] = str(year)
            for item in self._get(f"/search/{kind}", **params).get("results", []):
                found.append((float(item.get("popularity") or 0), _match(kind, item)))  # type: ignore[arg-type]
        found.sort(key=lambda pair: pair[0], reverse=True)
        return [match for _, match in found[:limit]]

    def details(self, kind: Kind, tmdb_id: int) -> Match:
        """详情，包括 IMDb 编号。"""
        return _match(kind, self._get(f"/{kind}/{tmdb_id}", append_to_response="external_ids"))

    def check(self) -> None:
        """测试 API Key。"""
        self._get("/configuration")
