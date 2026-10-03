import httpx
import pytest

from whatdvd.indexer import DVD9_MAX_BYTES, IndexerError, Jackett, classify, parse_torznab

# 结构同 Jackett 1.x 对 rutor 的 Torznab 输出，标题和链接为虚构
FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <title>RuTor</title>
    <item>
      <title>Фильм / Some Film (2002) DVD9 | P -Custom</title>
      <guid>https://d.rutor.info/download/1</guid>
      <jackettindexer id="rutor">RuTor</jackettindexer>
      <comments>https://rutor.info/torrent/1/some-film</comments>
      <pubDate>Thu, 20 Aug 2026 21:00:00 +0000</pubDate>
      <size>7945689497</size>
      <link>http://jackett:9117/dl/rutor/?jackett_apikey=KEY&amp;path=abc&amp;file=Some+Film</link>
      <enclosure url="http://jackett:9117/dl/rutor/?jackett_apikey=KEY&amp;path=abc" length="7945689497" type="application/x-bittorrent" />
      <torznab:attr name="seeders" value="3" />
      <torznab:attr name="infohash" value="56D11DB8EB76B75BBE6BBD12A61400BA6E50FF71" />
      <torznab:attr name="magneturl" value="magnet:?xt=urn:btih:56d11db8eb76b75bbe6bbd12a61400ba6e50ff71&amp;dn=x" />
    </item>
    <item>
      <title>Other Film (1999) DVD5</title>
      <guid>https://d.rutor.info/download/2</guid>
      <size>4000000000</size>
    </item>
  </channel>
</rss>"""


def test_parse_torznab() -> None:
    first, second = parse_torznab(FEED)
    assert first.title == "Фильм / Some Film (2002) DVD9 | P -Custom"
    assert first.indexer == "RuTor" and first.size == 7945689497 and first.seeders == 3
    assert first.info_hash == "56d11db8eb76b75bbe6bbd12a61400ba6e50ff71"
    assert first.magnet is not None and first.magnet.startswith("magnet:?xt=urn:btih:56d1")
    assert first.details_url == "https://rutor.info/torrent/1/some-film"
    assert first.download_url == "http://jackett:9117/dl/rutor/?jackett_apikey=KEY&path=abc&file=Some+Film"
    assert first.published == 1787259600.0
    # 缺字段时的默认值；没有 jackettindexer 时用频道名
    assert (second.indexer, second.published, second.magnet, second.info_hash, second.seeders) == (
        "RuTor", None, None, None, None
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('<?xml version="1.0"?><error code="100" description="Invalid API Key" />', "Invalid API Key"),
        ('<error code="201" description="Indexer is not configured" />', "Indexer is not configured"),
        ("not xml", "无法解析"),
    ],
)
def test_parse_torznab_errors(body: str, message: str) -> None:
    with pytest.raises(IndexerError, match=message):
        parse_torznab(body)


def _jackett(handler: httpx.MockTransport) -> Jackett:
    return Jackett("http://jackett:9117/", "KEY", indexer="rutor", transport=handler)


def _film_item(guid: str, indexer: str, *cats: int) -> str:
    categories = "".join(f"<category>{c}</category>" for c in cats)
    return (f"<item><title>Film {guid} DVD9</title><guid>{guid}</guid>"
            f"<jackettindexer id='{indexer}'>{indexer}</jackettindexer><size>1</size>{categories}</item>")


def test_search_films_only() -> None:
    """只要影视类：请求电影、电视、其他三类，再去掉标为其他、体育的；Jackett 的 rutor 不区分分类，全部保留。"""
    seen: list[str | None] = []
    feed = (
        "<rss><channel><title>x</title>"
        + _film_item("movie", "rutracker", 2070, 100101)
        + _film_item("series", "rutracker", 5000, 100921)
        + _film_item("sport", "rutracker", 5060, 100283)
        + _film_item("game-bonus", "rutracker", 8000, 100003)
        + _film_item("rutor", "rutor", 8000, 100003)
        + "</channel></rss>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("cat"))
        return httpx.Response(200, text=feed)

    transport = httpx.MockTransport(handler)
    films = Jackett("http://jackett:9117", "KEY", films_only=True, transport=transport).search("DVD9")
    assert [r.guid for r in films] == ["movie", "series", "rutor"]
    assert films[0].categories == (2070, 100101) and films[0].indexer_id == "rutracker"
    everything = Jackett("http://jackett:9117", "KEY", transport=transport).search("DVD9")
    assert len(everything) == 5
    assert seen == ["2000,5000,8000", None]


def test_search_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=FEED)

    assert len(_jackett(httpx.MockTransport(handler)).search("DVD9")) == 2
    url = seen[0].url
    assert url.path == "/api/v2.0/indexers/rutor/results/torznab/api"
    assert dict(url.params) == {"apikey": "KEY", "t": "search", "q": "DVD9"}


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(500, json={"result": "error", "error": "Unknown indexer: rutor"}), "Unknown indexer"),
        (httpx.Response(400, text='<error code="100" description="Invalid API Key" />'), "Invalid API Key"),
        (httpx.Response(502, text="Bad Gateway"), "HTTP 502"),
    ],
)
def test_search_errors(response: httpx.Response, message: str) -> None:
    with pytest.raises(IndexerError, match=message):
        _jackett(httpx.MockTransport(lambda request: response)).search("DVD9")


def test_search_unreachable() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(IndexerError, match="连不上 Jackett"):
        _jackett(httpx.MockTransport(fail)).search("DVD9")


def test_fetch_torrent_and_magnet_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("path") == "magnet":
            return httpx.Response(302, headers={"location": "magnet:?xt=urn:btih:" + "a" * 40})
        if request.url.params.get("path") == "bad":
            return httpx.Response(200, text="<html>login</html>")
        return httpx.Response(200, content=b"d4:infod4:name1:aee")

    jackett = _jackett(httpx.MockTransport(handler))
    assert jackett.fetch("http://jackett:9117/dl/rutor/?path=file") == b"d4:infod4:name1:aee"
    assert jackett.fetch("http://jackett:9117/dl/rutor/?path=magnet") == "magnet:?xt=urn:btih:" + "a" * 40
    with pytest.raises(IndexerError, match="下载种子失败"):
        jackett.fetch("http://jackett:9117/dl/rutor/?path=bad")


# ---------- 过滤：标题写法来自 rutor 上 DVD5 / DVD9 搜索结果的实际格式 ----------


@pytest.mark.parametrize(
    ("title", "reason"),
    [
        ("Фильм / Film (2002) DVD9 | P -Custom", "Custom"),
        ("Фильм / Film (1926) DVD9 | Sub-Custom", "Custom"),
        ("Фильм / Film (2002) DVD9 | D, P | Custom | iTunes", "Custom"),
        ("Фильм / Film (2005) DVD5 | P2-сжатый", "压缩过的盘"),
        ("Фильм / Film (2002) DVD5-Сжатый", "压缩过的盘"),
        ("Фильм (1947) DVD5-Реставрация", "修复版"),
        ("Снежная королева [1938-1988, СССР, мультфильмы, Betacam SP > DVD5]", "转制"),
        ("Фильм / Film [1985, США, VHS > DVD9]", "转制"),
        ("Film (2001) DVDRip", "不是 DVD 原盘"),
        ("Film (2001) BDRemux 1080p", "不是 DVD 原盘"),
        ("Film (2001) Blu-ray DVD9", "不是 DVD 原盘"),
        ("Film (2001) WEB-DL 720p", "不是 DVD 原盘"),
        ("Film (2001) HDTVRip", "不是 DVD 原盘"),
        ("Film (2001) [MPEG-2]", "没有 DVD5"),
    ],
)
def test_classify_excluded(title: str, reason: str) -> None:
    verdict = classify(title, 4_000_000_000)
    assert not verdict.accepted and verdict.reason is not None and reason in verdict.reason


@pytest.mark.parametrize(
    ("title", "kind", "discs"),
    [
        ("Фильм / Film (2002) DVD9", "DVD9", 1),
        ("Film (2005) DVD5 | P", "DVD5", 1),
        ("Film (2005) DVD-9", "DVD9", 1),
        ("Film 2005 DVD9", "DVD9", 1),  # 年份不算盘数
        ("Film (2005) 2 DVD9-FireRoke", "2×DVD9", 2),
        ("Film (2005) 2 х DVD9", "2×DVD9", 2),  # 西里尔字母 х
        ("Film (2005) 2xDVD9", "2×DVD9", 2),
        ("Film (2005) DVD9+DVD5-FireRoke", "DVD9+DVD5", 2),
        ("Film (2005) DVD9, DVD5 | Р2", "DVD9+DVD5", 2),
        ("Film (2005) 2 DVD9 1DVD5", "2×DVD9+DVD5", 3),
        ("Film (2005) 4 DVD9-FireRoke", "4×DVD9", 4),
        # kinozal 写 "DVD-9"，数量写在前面
        ("Life in the undergrowth - 2005  VO, Sub 2 x DVD-9", "2×DVD9", 2),
        ("Film 2003 DUB, Sub DVD-9", "DVD9", 1),
        # rutracker 常把盘型写两遍（原名和译名各一次），只算一张
        ("Сериал [DVD9] S2E20-22 / Twin Peaks [1990, DVD9]", "DVD9", 1),
        ("Film [1995, DVD9+DVD5] / Фильм [DVD9]", "DVD9+DVD5", 2),
    ],
)
def test_classify_disc_count(title: str, kind: str, discs: int) -> None:
    verdict = classify(title, 1)
    assert verdict.accepted and (verdict.kind, verdict.discs) == (kind, discs)


@pytest.mark.parametrize(
    ("title", "note"),
    [
        ("Film (2002) DVD9 | D, P, A-FullScreen", "带俄语配音标记（D, P, A）"),
        ("Film (2002) DVD5 | Р, А", "带俄语配音标记（P, A）"),  # 西里尔字母
        ("Film (2002) DVD5 | P2, L1", "带俄语配音标记（P2, L1）"),
        ("Film (2002) DVD5 | A-PanScan", "Pan & Scan"),
        # rutor 网页上的原始写法（直连时），配音标记不一定在最后一段
        ("Film (2002) DVD9 | D, P, A | FullScreen", "带俄语配音标记（D, P, A）"),
        ("Film (2002) DVD9 | P2 | Лицензия", "带俄语配音标记（P2）"),
    ],
)
def test_classify_notes(title: str, note: str) -> None:
    verdict = classify(title, 1)
    assert verdict.accepted and any(note in n for n in verdict.notes), verdict.notes


@pytest.mark.parametrize(
    ("title", "codes"),
    [
        ("I Spit on Your Grave [2010, DVD9] Dub + AVO + Sub Rus, Eng + Original Eng", "Dub, AVO"),
        ("Mallrats [1995, DVD9] 2x MVO + VO + Sub Rus, Eng", "MVO, VO"),
        ("Duplex 2003 DUB, Sub DVD-9", "Dub"),
        ("Heimsendir - 2011  MVO (Ozz), Sub DVD-5", "MVO"),
    ],
)
def test_classify_voice_codes_rutracker_kinozal(title: str, codes: str) -> None:
    assert f"带俄语配音标记（{codes}）" in classify(title, 1).notes[0]


def test_classify_voice_codes_need_whole_words() -> None:
    # "VOB"、"Volume"、"DVO" 以外的词不算
    assert classify("Film [2001, DVD9] VOB Volume 2 Original Eng", 1).notes == []


@pytest.mark.parametrize(
    ("title", "stripped"),
    [
        ("DVD-5", True),  # 全俄文标题被删光
        ("12 13 13 (V.Ray release) [DVD9] S2E20-22 + / Twin Peaks [1990, / / DVD9]", True),
        ("Фильм / Film (2002) DVD9", False),
        ("Film (2002) DVD9", False),
    ],
)
def test_classify_detects_stripped_cyrillic(title: str, stripped: bool) -> None:
    notes = classify(title, 1).notes
    assert any("Strip Cyrillic Letters" in n for n in notes) is stripped


def test_classify_no_dub_note_for_other_tails() -> None:
    assert classify("Film (2002) DVD5 | Полная версия", 1).notes == []
    assert classify("Film (2002) DVD5 | Кармен Видео", 1).notes == []


def test_classify_size_and_seeders() -> None:
    assert classify("Film DVD9", DVD9_MAX_BYTES).notes == []
    assert "体积超出 DVD9 的容量" in classify("Film DVD9", DVD9_MAX_BYTES + 1).notes[0]
    assert classify("Film 2 DVD9", 2 * DVD9_MAX_BYTES).notes == []
    assert classify("Film DVD5", 4_000_000_000, seeders=0).notes == ["目前没有做种者"]


def test_search_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("whatdvd.indexer.time.sleep", sleeps.append)
    jackett = Jackett("http://j", "k", delay=2.0, transport=httpx.MockTransport(lambda r: httpx.Response(200, text=FEED)))
    jackett.search("DVD9")
    jackett.search("DVD9 1999")
    jackett.search("DVD9 1998")
    assert len(sleeps) == 2 and all(0 < s <= 2.0 for s in sleeps)  # 第一次不等


@pytest.mark.parametrize(
    ("raw", "title"),
    [
        ("Чайка 1971 РУ DVD-5 - RUSSIAN", "Чайка 1971 РУ DVD-5"),
        ("Film [2001, DVD9] RUS", "Film [2001, DVD9]"),
        ("Russian Ark [2002, DVD9]", "Russian Ark [2002, DVD9]"),
        ("Film RUSSIAN DVD9", "Film RUSSIAN DVD9"),
    ],
)
def test_parse_torznab_strips_jackett_language_suffix(raw: str, title: str) -> None:
    feed = f"<rss><channel><title>K</title><item><title>{raw}</title><guid>1</guid></item></channel></rss>"
    assert parse_torznab(feed)[0].title == title


@pytest.mark.parametrize(
    ("title", "labels"),
    [
        ("Чайка 1971 РУ DVD-5", ["俄语原版片（РУ），没有后加配音"]),
        ("Andre Rieu - Live in Dublin 2003 БП DVD-5", ["原声，没有翻译（БП）"]),
        ("Film (2002) DVD9 от New-Team | D-Лицензия", ["俄罗斯正版盘（Лицензия）"]),
        ("Film (2002) DVD9 | Лицензия", ["俄罗斯正版盘（Лицензия）"]),
        # 只认单独出现的缩写
        ("Русалка (2007) DVD9", []),
        ("Трубка (2007) DVD9", []),
        ("Film (2002) DVD9", []),
    ],
)
def test_classify_labels(title: str, labels: list[str]) -> None:
    verdict = classify(title, 1)
    assert verdict.labels == labels
    assert not any("正版" in n or "原声" in n or "原版" in n for n in verdict.notes)  # 正面标记不算提示


DVD_FILES = ["VIDEO_TS/VIDEO_TS.IFO", "VIDEO_TS/VTS_01_1.VOB"]


@pytest.mark.parametrize(
    ("name", "files", "reason"),
    [
        # rutor 上标题没写 Custom、种子文件夹名写了的（2026 年 10 月实际抽查到的）
        ("Terminator.2.Judgment.Day.(1991).(DVD9.CUSTOM.FS.2xMVO.AVO.Eng.Sub)", DVD_FILES, "Custom"),
        ("Lethal.Weapon.(1987).(DVD9.Custom.NTSC.FS.2xDUB.MVO.3xAVO)", DVD_FILES, "Custom"),
        ("Saving.Private.Ryan.1998.DVD9.(custom)", DVD_FILES, "Custom"),
        ("Batman.&.Robin.(1997)(DVD5.Custom.NTSC.FS.DUB.Varus)", DVD_FILES, "Custom"),
        ("Film.2005.DVD5.сжатый", DVD_FILES, "压缩"),
        ("Film.2005.DVDRip", ["Film.avi"], "不是 DVD 原盘"),
        ("Film 2005", ["Film.mkv", "Film.srt"], "没有 VOB、IFO 或 ISO"),
        # 正常的
        ("Juriev.den.2008.O.DVD_RUSSFILM", DVD_FILES, None),
        ("2k2", DVD_FILES, None),
        ("VIDEO_TS", ["VIDEO_TS.IFO", "VTS_01_1.VOB"], None),
        ("Save and Protect (original version) [DVD9]", DVD_FILES, None),
        ("RAPA_NUI-1994", DVD_FILES, None),
        ("Film.iso", ["Film.iso"], None),
        ("Жизнь как чудо", DVD_FILES, None),
    ],
)
def test_inspect_contents(name: str, files: list[str], reason: str | None) -> None:
    from whatdvd.indexer import inspect_contents

    found = inspect_contents(name, files)
    assert (found is None) if reason is None else (found is not None and reason in found)
