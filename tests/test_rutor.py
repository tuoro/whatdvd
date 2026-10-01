"""rutor 直连：页面结构同 rutor.info 的搜索页（2026 年），标题为虚构。"""

import datetime

import httpx
import pytest

from whatdvd.indexer import IndexerError
from whatdvd.rutor import SOURCE, Rutor, RutorFormatError, parse_search


def row(torrent_id: int, title: str, size: str = "7.40&nbsp;GB", seeders: int = 3, comments: bool = False,
        date: str = "21&nbsp;Авг&nbsp;26") -> str:
    info_hash = f"{torrent_id:040x}"
    title_cell = '<td colspan = "2">' if not comments else "<td >"
    comment_cell = '<td align="right">2<img src="//cdn/i/com.gif" alt="C" /></td>\n' if comments else ""
    return (
        f'<tr class="gai"><td>{date}</td>{title_cell}'
        f'<a class="downgif" href="//d.rutor.info/download/{torrent_id}"><img src="//cdn/i/d.gif" alt="D" /></a>'
        f'<a href="magnet:?xt=urn:btih:{info_hash}&dn=rutor.info&tr=udp://opentor.net:6969">'
        f'<img src="//cdn/i/m.png" alt="M" /></a>\n'
        f'<a href="/torrent/{torrent_id}/slug-{torrent_id}">{title} </a></td> {comment_cell}'
        f'<td align="right">{size}</td><td align="center"><span class="green">'
        f'<img src="//cdn/t/arrowup.gif" alt="S" />&nbsp;{seeders}</span>&nbsp;'
        f'<img src="//cdn/t/arrowdown.gif" alt="L" /><span class="red">&nbsp;0</span></td></tr>'
    )


def page(total: int, rows: list[str]) -> str:
    news = '<tr><td class="news_date">22-Апр</td><td class="news_title"><a href="/torrent/472">Новый Адрес</a></td></tr>'
    return (
        f"<html><table>{news}</table><div id=\"index\"><b>Страницы: </b> Результатов поиска {total} (max. 2000)"
        f"<table width=\"100%\">{''.join(rows)}</table></div></html>"
    )


def test_parse_search_rows() -> None:
    html = page(2000, [
        row(1103505, "Фильм / Film (2002) DVD9 | P | Custom"),
        row(679752, "Гора / Der Berg (1926) DVD9 | Sub", size="6.15&nbsp;GB", seeders=1, comments=True,
            date="31&nbsp;Май&nbsp;09"),
        row(5, "Мелочь &amp; Co (1999) DVD5", size="512.30&nbsp;MB", seeders=0),
    ])
    total, releases = parse_search(html, "https://rutor.info")
    assert total == 2000 and len(releases) == 3  # 新闻行不算
    first, second, third = releases
    assert first.title == "Фильм / Film (2002) DVD9 | P | Custom"
    assert first.size == int(7.40 * 1024**3) and first.seeders == 3
    assert first.info_hash == f"{1103505:040x}" and first.magnet and first.magnet.startswith("magnet:?xt=urn:btih:")
    assert first.download_url == "https://d.rutor.info/download/1103505"
    assert first.details_url == "https://rutor.info/torrent/1103505/slug-1103505"
    assert first.guid == "https://rutor.info/torrent/1103505" and first.indexer == SOURCE
    assert first.published == datetime.datetime(2026, 8, 21, tzinfo=datetime.timezone.utc).timestamp()
    # 有评论数的行多一列
    assert (second.size, second.seeders) == (int(6.15 * 1024**3), 1)
    assert second.published == datetime.datetime(2009, 5, 31, tzinfo=datetime.timezone.utc).timestamp()
    assert third.title == "Мелочь & Co (1999) DVD5" and third.size == int(512.30 * 1024**2)


def test_parse_search_empty_and_changed_layout() -> None:
    assert parse_search(page(0, []), "https://rutor.info") == (0, [])
    with pytest.raises(RutorFormatError, match="改版"):
        parse_search("<html>Сайт на обслуживании</html>", "https://rutor.info")
    broken = page(1, ['<tr class="gai"><td>1&nbsp;Янв&nbsp;26</td><td>что-то другое</td></tr>'])
    with pytest.raises(RutorFormatError, match="格式变了"):
        parse_search(broken, "https://rutor.info")


class FakeRutor:
    def __init__(self, total: int, music: frozenset[int] = frozenset()) -> None:
        """music：属于音乐分类（2）的资源编号；其他非零分类没有结果。"""
        self.total = total
        self.music = music
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.raw_path.decode())
        if request.url.host == "d.rutor.info":
            return httpx.Response(200, content=b"d4:infod4:name1:aee")
        parts = request.url.path.split("/")  # /search/<页码>/<分类>/100/0/<关键词>
        number, category = int(parts[2]), int(parts[3])
        if category:
            ids = sorted(self.music) if category == 2 else []
            chunk = ids[number * 100 : number * 100 + 100]
            return httpx.Response(200, text=page(len(ids), [row(i, f"Music {i} DVD9") for i in chunk]))
        start = number * 100
        rows = [row(start + i + 1, f"Film {start + i} DVD9") for i in range(min(100, max(self.total - start, 0)))]
        return httpx.Response(200, text=page(self.total, rows))


def test_search_all_follows_pages() -> None:
    fake = FakeRutor(total=250)
    client = Rutor("https://rutor.is/", delay=0, transport=httpx.MockTransport(fake))
    releases = client.search_all("DVD9 1999")
    assert len(releases) == 250 and len({r.info_hash for r in releases}) == 250
    assert fake.requests == [f"/search/{n}/0/100/0/DVD9%201999" for n in range(3)]
    assert releases[0].details_url.startswith("https://rutor.is/torrent/")


def test_search_all_stops_at_twenty_pages() -> None:
    fake = FakeRutor(total=2000)
    client = Rutor(delay=0, transport=httpx.MockTransport(fake))
    progress: list[tuple[int, int]] = []
    assert len(client.search_all("DVD9", lambda done, total: progress.append((done, total)))) == 2000
    assert len(fake.requests) == 20 and progress[-1] == (20, 20)


def test_delay_between_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("whatdvd.rutor.time.sleep", sleeps.append)
    client = Rutor(delay=0.7, transport=httpx.MockTransport(FakeRutor(total=150)))
    client.search_all("DVD9")
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 0.7  # 第二次请求前等待


def test_fetch_torrent() -> None:
    client = Rutor(delay=0, transport=httpx.MockTransport(FakeRutor(total=0)))
    assert client.fetch("https://d.rutor.info/download/1") == b"d4:infod4:name1:aee"
    bad = Rutor(delay=0, transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")))
    with pytest.raises(IndexerError, match="下载种子失败"):
        bad.fetch("https://d.rutor.info/download/1")


@pytest.mark.parametrize(
    ("response", "message"),
    [(httpx.Response(503, text="busy"), "HTTP 503"), (httpx.Response(200, text="<html></html>"), "改版")],
)
def test_search_errors(response: httpx.Response, message: str) -> None:
    client = Rutor(delay=0, transport=httpx.MockTransport(lambda r: response))
    with pytest.raises(IndexerError, match=message):
        client.search("DVD9")


def test_unreachable() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(IndexerError, match="连不上 rutor"):
        Rutor(delay=0, transport=httpx.MockTransport(fail)).search("DVD9")


@pytest.mark.parametrize("all_pages", [False, True])
def test_films_drops_non_film_categories(all_pages: bool) -> None:
    """rutor 搜索一次只能选一个分类：搜音乐、其他、体育三个分类，再从全部结果中剔除。"""
    fake = FakeRutor(total=150, music=frozenset({2, 120}))
    client = Rutor(delay=0, transport=httpx.MockTransport(fake))
    releases = client.films("DVD9", all_pages)
    kept = {int(r.guid.rsplit("/", 1)[1]) for r in releases}
    expected = set(range(1, 151 if all_pages else 101)) - {2, 120}
    assert kept == expected
    searched = {path.split("/")[3] for path in fake.requests}
    assert searched == {"0", "2", "3", "13"}
