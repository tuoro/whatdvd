"""TMDB 客户端：模拟的 API，不访问网络。"""

from typing import Any

import httpx
import pytest

from whatdvd.tmdb import Tmdb, TmdbError

MOVIE = {"id": 9426, "title": "The Emerald Forest", "original_title": "The Emerald Forest", "original_language": "en",
         "release_date": "1985-07-03", "popularity": 9.5}
RU_MOVIE = {"id": 25237, "title": "Come and See", "original_title": "Иди и смотри", "original_language": "ru",
            "release_date": "1985-10-17", "popularity": 20.1}
SERIES = {"id": 1920, "name": "Twin Peaks", "original_name": "Twin Peaks", "original_language": "en",
          "first_air_date": "1990-04-08", "popularity": 30.0}


class FakeTmdb:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"status_message": "Invalid API key"})
        path = request.url.path
        if path == "/3/search/movie":
            return httpx.Response(200, json={"results": [MOVIE, RU_MOVIE]})
        if path == "/3/search/tv":
            return httpx.Response(200, json={"results": [SERIES]})
        if path == "/3/movie/25237":
            return httpx.Response(200, json={**RU_MOVIE, "external_ids": {"imdb_id": "tt0091251"}})
        if path == "/3/tv/1920":
            return httpx.Response(200, json={**SERIES, "external_ids": {"imdb_id": "tt0098936"}})
        if path == "/3/find/tt0091251":
            assert request.url.params["external_source"] == "imdb_id"
            return httpx.Response(200, json={"movie_results": [RU_MOVIE], "tv_results": []})
        if path.startswith("/3/find/"):
            return httpx.Response(200, json={"movie_results": [], "tv_results": []})
        if path == "/3/configuration":
            return httpx.Response(200, json={"images": {}})
        return httpx.Response(404, json={})


def _client(fake: FakeTmdb, key: str = "v3key") -> Tmdb:
    return Tmdb(key, transport=httpx.MockTransport(fake))


def test_search_movies_and_series_by_popularity() -> None:
    fake = FakeTmdb()
    results = _client(fake).search("Twin", 1985)
    assert [(m.kind, m.title, m.year) for m in results] == [
        ("tv", "Twin Peaks", 1990), ("movie", "Come and See", 1985), ("movie", "The Emerald Forest", 1985),
    ]
    movie, tv = fake.requests
    assert movie.url.params["year"] == "1985" and tv.url.params["first_air_date_year"] == "1985"
    assert movie.url.params["api_key"] == "v3key" and movie.url.params["language"] == "en-US"
    assert "authorization" not in movie.headers


def test_v4_token_goes_in_header() -> None:
    fake = FakeTmdb()
    _client(fake, "eyJhbGciOi.token").check()
    request = fake.requests[0]
    assert request.headers["authorization"] == "Bearer eyJhbGciOi.token" and "api_key" not in request.url.params


@pytest.mark.parametrize(
    ("kind", "tmdb_id", "expected"),
    [
        ("movie", 25237, {"title": "Come and See", "original_title": "Иди и смотри", "year": 1985, "imdb_id": "tt0091251"}),
        ("tv", 1920, {"title": "Twin Peaks", "original_title": "Twin Peaks", "year": 1990, "imdb_id": "tt0098936"}),
    ],
)
def test_details_include_imdb(kind: Any, tmdb_id: int, expected: dict[str, Any]) -> None:
    match = _client(FakeTmdb()).details(kind, tmdb_id)
    data = match.public()
    assert {k: data[k] for k in expected} == expected
    assert data["imdb_url"] == f"https://www.imdb.com/title/{expected['imdb_id']}/"
    assert data["url"] == f"https://www.themoviedb.org/{kind}/{tmdb_id}"


@pytest.mark.parametrize(("status", "message"), [(401, "API Key 无效"), (500, "HTTP 500")])
def test_errors(status: int, message: str) -> None:
    with pytest.raises(TmdbError, match=message):
        _client(FakeTmdb(status)).search("x")


def test_unreachable() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(TmdbError, match="连不上 TMDB"):
        Tmdb("k", transport=httpx.MockTransport(fail)).check()


def test_find_by_imdb_id() -> None:
    client = _client(FakeTmdb())
    [match] = client.find_imdb("tt0091251")
    assert (match.kind, match.id, match.title, match.imdb_id) == ("movie", 25237, "Come and See", "tt0091251")
    assert client.find_imdb("tt0035118") == []
