"""IMDb 数据集：用模拟的小数据集测试边下载边导入和取名规则，不访问网络。"""

import gzip
import threading
from pathlib import Path

import httpx
import pytest

from whatdvd.imdb_dataset import Cancelled, DatasetError, ImdbDataset, build

BASICS = [
    "tconst\ttitleType\tprimaryTitle\toriginalTitle\tisAdult\tstartYear\tendYear\truntimeMinutes\tgenres",
    "tt0091251\tmovie\tCome and See\tIdi i smotri\t0\t1985\t\\N\t142\tDrama,War",
    "tt0083658\tmovie\tBlade Runner\tBlade Runner\t0\t1982\t\\N\t117\tAction,Drama",
    "tt0079944\tmovie\tStalker\tStalker\t0\t1979\t\\N\t162\tDrama,Sci-Fi",
    "tt0098936\ttvSeries\tTwin Peaks\tTwin Peaks\t0\t1990\t1991\t47\tCrime,Drama",
    "tt0000001\tshort\tCarmencita\tCarmencita\t0\t1894\t\\N\t1\tDocumentary,Short",
    "tt0098935\ttvEpisode\tPilot\tPilot\t0\t1990\t\\N\t94\tDrama",
    "tt0000002\tmovie\tNo Year\t\\N\t0\t\\N\t\\N\t\\N\t\\N",
    "tt0183022\tmovie\tDva kapitana II\tDva kapitana II\t0\t1992\t\\N\t\\N\tDrama",
]
AKAS = [
    "titleId\tordering\ttitle\tregion\tlanguage\ttypes\tattributes\tisOriginalTitle",
    "tt0079944\t1\tСталкер\tRU\t\\N\timdbDisplay\t\\N\t0",
    "tt0079944\t2\tStalker (US)\tUS\t\\N\t\\N\t\\N\t0",
    "tt0079944\t3\tStalker the Film\tXWW\ten\timdbDisplay\t\\N\t0",
    "tt0079944\t4\tStalker: Director's Cut\tXWW\ten\timdbDisplay\tdirector's cut\t0",
    "tt0083658\t1\tBlade Runner: The Final Cut\tUS\t\\N\timdbDisplay\t\\N\t0",
    "tt0091251\t1\tCome and See\tXWW\ten\timdbDisplay\t\\N\t0",
    "tt0098935\t1\tPilot (US)\tUS\t\\N\timdbDisplay\t\\N\t0",
    "tt0083658\t2\tWorking Title\tUS\t\\N\tworking\t\\N\t0",
    "tt0091251\t2\tИди и смотри\tSUHH\tru\timdbDisplay\t\\N\t0",
    "tt0079944\t5\tСталкер\tSUHH\tru\timdbDisplay\t\\N\t0",
    "tt0098935\t2\tПилот\tRU\t\\N\timdbDisplay\t\\N\t0",
]


def _gz(lines: list[str]) -> bytes:
    return gzip.compress(("\n".join(lines) + "\n").encode())


def _transport(basics: list[str] = BASICS, status: int = 200) -> httpx.MockTransport:
    files = {"/title.basics.tsv.gz": _gz(basics), "/title.akas.tsv.gz": _gz(AKAS)}

    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status)
        body = files[request.url.path]
        headers = {"content-length": str(len(body)), "last-modified": "Sat, 03 Oct 2026 00:48:43 GMT"}
        stream = httpx.ByteStream(b"" if request.method == "HEAD" else body)
        return httpx.Response(200, stream=stream, headers=headers)

    return httpx.MockTransport(handler)


@pytest.fixture
def dataset(tmp_path: Path) -> ImdbDataset:
    path = tmp_path / "imdb.db"
    phases: list[str] = []
    stats = build(path, lambda phase, done, total: phases.append(phase), base_url="https://imdb.test",
                  transport=_transport())
    assert stats["titles"] == 6 and stats["english_titles"] == 3  # 短片和单集不要
    assert phases[0].startswith("下载并导入 title.basics") and "title.akas" in phases[-2] and phases[-1] == "整理数据"
    return ImdbDataset(path)


def test_info(dataset: ImdbDataset) -> None:
    info = dataset.info()
    assert info is not None
    assert (info["titles"], info["english_titles"], info["source_date"]) == (6, 3, "Sat, 03 Oct 2026 00:48:43 GMT")
    assert info["searchable"] is True
    assert info["bytes"] > 0 and info["imported_at"] > 0


@pytest.mark.parametrize(
    ("imdb_id", "language", "title", "original", "year"),
    [
        ("tt0091251", "ru", "Come and See", "Idi i smotri", 1985),  # IMDb 显示的已经是英文名
        ("tt0083658", "en", "Blade Runner", "Blade Runner", 1982),  # 英语片不用地区片名（US 的是剪辑版名）
        ("tt0079944", "ru", "Stalker the Film", "Stalker", 1979),  # 显示原名的非英语片：XWW 优先，带附注的不要
        ("tt0079944", "", "Stalker", "Stalker", 1979),  # 不知道语言时用 IMDb 显示的名字
        ("tt0098936", "en", "Twin Peaks", "Twin Peaks", 1990),
        ("tt0000002", "en", "No Year", "No Year", None),
    ],
)
def test_title(dataset: ImdbDataset, imdb_id: str, language: str, title: str, original: str, year: int | None) -> None:
    found = dataset.title(imdb_id, language)
    assert found is not None
    assert (found.title, found.original_title, found.year) == (title, original, year)


def test_missing(dataset: ImdbDataset, tmp_path: Path) -> None:
    assert dataset.title("tt0000001") is None  # 短片没有导入
    assert dataset.title("tt9999999") is None
    assert dataset.title("nonsense") is None
    empty = ImdbDataset(tmp_path / "none.db")
    assert empty.info() is None and empty.title("tt0091251") is None


def test_failed_update_keeps_old_data(dataset: ImdbDataset) -> None:
    with pytest.raises(DatasetError, match="HTTP 503"):
        build(dataset.path, lambda *a: None, base_url="https://imdb.test", transport=_transport(status=503))
    with pytest.raises(DatasetError, match="没有读到"):
        build(dataset.path, lambda *a: None, base_url="https://imdb.test", transport=_transport(basics=BASICS[:1]))
    assert dataset.title("tt0091251") is not None
    assert not list(dataset.path.parent.glob("*.importing"))


def test_cancel(tmp_path: Path) -> None:
    stop = threading.Event()
    stop.set()
    with pytest.raises(Cancelled):
        build(tmp_path / "imdb.db", lambda *a: None, stop=stop, base_url="https://imdb.test", transport=_transport())
    assert not (tmp_path / "imdb.db").exists()


@pytest.mark.parametrize(
    ("query", "year", "expected"),
    [
        ("Come and See", 1985, "tt0091251"),  # IMDb 显示的名字
        ("Иди и смотри", 1985, "tt0091251"),  # 苏联地区的俄文名
        ("Idi i smotri", None, "tt0091251"),  # 原名
        ("СТАЛКЕР", 1979, "tt0079944"),  # 大小写
        ("Blade Runner: The Final Cut", None, "tt0083658"),  # 地区片名也能查
        ("Два капитана 2", 1992, "tt0183022"),  # 俄文转写 + 罗马数字
        ("Come and See. Special Edition", 1985, "tt0091251"),  # 去掉副标题
    ],
)
def test_find(dataset: ImdbDataset, query: str, year: int | None, expected: str) -> None:
    hits = dataset.find(query, year)
    assert hits and hits[0].imdb_id == expected


def test_find_ranks_year_and_skips_episodes(dataset: ImdbDataset) -> None:
    assert dataset.find("Пилот") == []  # 单集没有导入，名字也不收录
    assert dataset.find("Nothing Like This") == []
    assert dataset.find("") == []


@pytest.mark.parametrize(
    ("name", "normalized"),
    [
        ("Terminator 2: Judgment Day", "terminator 2 judgment day"),
        ("Stara baśń. Kiedy słońce było bogiem", "stara basn kiedy slonce bylo bogiem"),
        ("Ёлки", "елки"),
        ("Rocky IV", "rocky 4"),
        ("Fast & Furious", "fast and furious"),
        ("Amélie", "amelie"),
    ],
)
def test_normalize(name: str, normalized: str) -> None:
    from whatdvd.imdb_dataset import normalize

    assert normalize(name) == normalized
