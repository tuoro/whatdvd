"""查重：站点上已有的 DVD 原盘（标题写法来自 Blutopia 的实际结果）。"""

import httpx
import pytest

from whatdvd.dupes import dvd_kind, existing_dvds
from whatdvd.indexer import Jackett, Release


@pytest.mark.parametrize(
    ("title", "kind"),
    [
        ("Come and See AKA Idi i smotri 1985 PAL 2xDVD9 DD 5.1", "2xDVD9"),
        ("Blade Runner 1982 Final Cut 2in1 EUR PAL 2xDVD9 DD 5.1-DHTs", "2xDVD9"),
        ("Blade Runner 1982 5in1 GBR PAL 5xDVD9 DD 5.1", "5xDVD9"),
        ("Film 2001 NTSC DVD5 DD 2.0", "DVD5"),
        ("Film 2001 PAL DVD9 + DVD5 DD 5.1", "DVD9+DVD5"),
        # 不是 DVD 原盘
        ("Come and See AKA Idi i smotri 1985 1080p GER Blu-ray AVC LPCM 2.0", None),
        ("Blade Runner 1982 Workprint 1080p HD DVD VC-1 DD+ 5.1", None),
        ("Film.2001.DVD9.Remux.mkv", None),
        ("Film 2001 DVDRip x264", None),
        ("Pulp.Fiction.1994.1080p.NF.WEB-DL.DDP5.1.H.264-NiSHKRiY0.mkv", None),
    ],
)
def test_dvd_kind(title: str, kind: str | None) -> None:
    assert dvd_kind(title) == kind


def _release(title: str) -> Release:
    return Release(guid=title, indexer="Blutopia", title=title, size=12_453_000_000, published=None,
                   details_url="https://blutopia.cc/torrents/1", download_url=None, magnet=None, info_hash=None,
                   seeders=3)


def test_existing_dvds_marks_same_kind_and_standard() -> None:
    releases = [_release(t) for t in (
        "Blade Runner 1982 5in1 GBR PAL 5xDVD9 DD 5.1",
        "Blade Runner 1982 Final Cut 2in1 EUR PAL 2xDVD9 DD 5.1-DHTs",
        "Blade Runner 1982 USA NTSC 2xDVD9 DD 5.1",
        "Blade Runner 1982 1080p Blu-ray Remux VC-1 DD 5.1",
    )]
    found = existing_dvds("blutopia-api", releases, "2xDVD9", "PAL")
    assert [(e.kind, e.standard, e.same) for e in found] == [
        ("2xDVD9", "PAL", True), ("5xDVD9", "PAL", False), ("2xDVD9", "NTSC", False)]
    assert found[0].url == "https://blutopia.cc/torrents/1" and found[0].seeders == 3
    # 不知道这张盘的制式时只比格式
    assert sum(e.same for e in existing_dvds("blutopia-api", releases, "2xDVD9", None)) == 2


def test_search_imdb_uses_movie_search() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, text="<rss><channel><title>Blutopia</title></channel></rss>")

    jackett = Jackett("http://jackett:9117", "KEY", indexer="blutopia-api", transport=httpx.MockTransport(handler))
    assert jackett.search_imdb("tt0091251") == []
    assert seen == [{"apikey": "KEY", "t": "movie", "imdbid": "tt0091251"}]
