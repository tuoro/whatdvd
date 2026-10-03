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


RATINGS = [
    "tconst\taverageRating\tnumVotes",
    "tt0091251\t8.4\t100000",
    "tt0083658\t8.1\t850000",
]


def _transport(
    basics: list[str] = BASICS, status: int = 200, akas: list[str] = AKAS, ratings: list[str] = RATINGS
) -> httpx.MockTransport:
    files = {"/title.basics.tsv.gz": _gz(basics), "/title.akas.tsv.gz": _gz(akas), "/title.ratings.tsv.gz": _gz(ratings)}

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
    assert phases[0].startswith("下载并导入 title.basics") and "title.ratings" in phases[-2] and phases[-1] == "整理数据"
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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Иди и смотри (1985) DVD9", "tt0091251"),
        ("Сталкер / Stalker (1979) DVD9 | P", "tt0079944"),
        ("Два капитана 2 (1992) DVD5", "tt0183022"),
        ("Сталкер (1990) DVD9", None),  # 年份对不上
        ("Unknown Film (2001) DVD9", None),
        ("Blade Runner DVD9", "tt0083658"),  # 没有年份，但只有一个结果
    ],
)
def test_confident(dataset: ImdbDataset, text: str, expected: str | None) -> None:
    hit, reason = dataset.confident(text)
    assert (hit.imdb_id if hit else None) == expected
    assert reason


# 同名同年的几个结果（IMDb 上的实际条目）
SAME_NAME = [
    "tt1798709\tmovie\tHer\tHer\t0\t2013\t\\N\t126\tDrama",
    "tt3512038\tvideo\tHer\tHer\t0\t2013\t\\N\t\\N\tShort",
    "tt2562232\tmovie\tBirdman or (The Unexpected Virtue of Ignorance)\tBirdman or (The Unexpected Virtue of Ignorance)\t0\t2014\t\\N\t119\tComedy",
    "tt5130912\tvideo\tBirdman\tBirdman\t0\t2014\t\\N\t\\N\tShort",
    "tt0482606\tmovie\tThe Strangers\tThe Strangers\t0\t2008\t\\N\t86\tHorror",
    "tt9000001\tmovie\tThe Strangers\tThe Strangers\t0\t2007\t\\N\t90\tDrama",
    "tt0882977\tmovie\tSnitch\tSnitch\t0\t2013\t\\N\t112\tAction",
    "tt30088382\tmovie\tSnitch\tSnitch\t0\t2013\t\\N\t95\tDrama",
    "tt3062096\tmovie\tInferno\tInferno\t0\t2016\t\\N\t121\tAction",
    "tt3855900\ttvMiniSeries\tInferno\tInferno\t0\t2016\t\\N\t\\N\tDrama",
    "tt9149142\tmovie\tInferno\tInferno\t0\t2017\t\\N\t\\N\tDrama",
]


MORE = [
    "tt0460681\ttvSeries\tSupernatural\tSupernatural\t0\t2005\t2020\t44\tDrama",
    "tt3508984\tmovie\tSupernatural\tSupernatural\t0\t2014\t\\N\t\\N\tDrama",
    "tt1853739\tmovie\tYou're Next\tYou're Next\t0\t2011\t\\N\t95\tHorror",
    "tt3976228\ttvSeries\tYou're Next\tYou're Next\t0\t2014\t\\N\t\\N\tComedy",
    "tt0089369\tmovie\tSolo Voyage\tIm Alleingang\t0\t1985\t\\N\t\\N\tDrama",
    "tt3480796\tmovie\tVice\tVice\t0\t2015\t\\N\t96\tAction",
    "tt6266538\tmovie\tVice\tVice\t0\t2018\t\\N\t132\tDrama",
    "tt3693866\ttvSeries\tWeekend\tWeekend\t0\t2014\t\\N\t\\N\tComedy",
    "tt0105236\tmovie\tReservoir Dogs\tReservoir Dogs\t0\t1992\t\\N\t99\tCrime",
    "tt9000002\tvideo\tReservoir Dogs: Sundance Institute 1991\tReservoir Dogs: Sundance Institute 1991\t0\t1991\t\\N\t\\N\tShort",
    "tt4183002\tmovie\tWeekend\tUik-end\t0\t2013\t\\N\t\\N\tCrime",
    "tt0209958\tmovie\tThe Cell\tThe Cell\t0\t2000\t\\N\t107\tHorror",
    "tt0057394\tmovie\tPacsirta\tPacsirta\t0\t1964\t\\N\t\\N\tDrama",
    "tt0314947\tmovie\tZhavoronok\tZhavoronok\t0\t1965\t\\N\t\\N\tWar",
    "tt0115083\ttvSeries\t7th Heaven\t7th Heaven\t0\t1996\t2007\t60\tDrama",
    "tt18686556\ttvMiniSeries\tSedmoye nebo\tSedmoye nebo\t0\t2006\t2006\t\\N\tDrama",
]
VOTES = [*RATINGS, "tt2562232\t7.7\t700000", "tt5130912\t6.0\t12", "tt0460681\t8.4\t500000",
         "tt3508984\t5.0\t108", "tt1853739\t6.5\t110000", "tt3976228\t7.0\t20", "tt3480796\t4.2\t20000",
         "tt6266538\t7.1\t200000", "tt1798709\t8.0\t700000", "tt3512038\t7.0\t15"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 靠别名对上的电影和同名 video：票数差得再多也不选（Saving Santa 该选 video，而电影的票数同样多得多）
        ("Бёрдмэн / Birdman (2014) DVD9", None),
        ("Бешеные псы / Reservoir Dogs [1991, США, DVD9]", "tt0105236"),  # 片名本身对上的电影（IMDb 记 1992 年）
        ("Сверхъестественное / Supernatural [S10] (2015) DVD5", "tt0460681"),  # 按季发布：开播年份的剧集
        ("Тебе конец! / You're Next (2013) DVD5", None),  # 年份只对得上同名剧集，2011 年的电影有名得多
        ("Добро пожаловать в рай / Vice (2015) DVD9", "tt3480796"),  # 年份完全一致：不因 2018 年那部更有名就不选
        ("Одиночное плавание / Im Alleingang / Solo Voyage (1985) DVD9", "tt0089369"),  # 第一个名字找不到，试下一个
        ("Она / Her (2013) DVD9", "tt1798709"),
        ("Уик-энд / Weekend (2014) DVD5", "tt4183002"),  # 没有季标记：不要同年的同名剧集
        # 只有俄文名：不要俄文译名恰好相同的外国片
        ("Жаворонок (1964) DVD5", "tt0314947"),  # 苏联的 Zhavoronok，不是匈牙利的 Pacsirta（俄文名也叫“Жаворонок”）
        ("Седьмое небо [01-04 из 04] (2006) DVD9", "tt18686556"),  # 转写 Sedmoe / IMDb 写 Sedmoye
        ("Клетка [01-04 из 04] (2001) DVD9", None),  # 剧集：IMDb 上对不上剧集，不选同名电影 The Cell
    ],
)
def test_confident_with_votes(tmp_path: Path, text: str, expected: str | None) -> None:
    akas = [*AKAS, "tt2562232\t1\tBirdman\tUS\t\\N\timdbDisplay\t\\N\t0",
            "tt0057394\t1\tЖаворонок\tRU\t\\N\timdbDisplay\t\\N\t0",
            "tt0115083\t1\tСедьмое небо\tRU\t\\N\timdbDisplay\t\\N\t0",
            "tt18686556\t1\tСедьмое небо\tRU\t\\N\timdbDisplay\t\\N\t0",
            "tt0209958\t1\tКлетка\tRU\t\\N\timdbDisplay\t\\N\t0",
            "tt9000002\t1\tReservoir Dogs\tUS\t\\N\t\\N\t\\N\t0"]
    build(tmp_path / "imdb.db", lambda *a: None, base_url="https://imdb.test",
          transport=_transport([*BASICS, *SAME_NAME, *MORE], akas=akas, ratings=VOTES))
    hit, reason = ImdbDataset(tmp_path / "imdb.db").confident(text)
    assert (hit.imdb_id if hit else None) == expected, reason


def test_old_dataset_without_votes(tmp_path: Path) -> None:
    """旧版本导入的数据集没有投票人数和结束年份：照常能查。"""
    import sqlite3

    path = tmp_path / "imdb.db"
    build(path, lambda *a: None, base_url="https://imdb.test", transport=_transport())
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE titles DROP COLUMN votes")
        db.execute("ALTER TABLE titles DROP COLUMN end_year")
    hit, _ = ImdbDataset(path).confident("Иди и смотри (1985) DVD9")
    assert hit is not None and hit.imdb_id == "tt0091251" and hit.votes == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Она / Her (2013) DVD9", "tt1798709"),  # 电影和同名 video：选电影
        ("Незнакомцы / The Strangers (2008) DVD9", "tt0482606"),  # 年份完全一致的
        ("Инферно / Inferno (2016) DVD5", "tt3062096"),  # 年份完全一致，再选电影
        ("Стукач / Snitch (2013) DVD5", None),  # 两部同名同年的电影
        ("Бёрдмэн / Birdman (2014) DVD9", None),  # 靠别名对上的电影和同名 video：分不出来，不选
    ],
)
def test_confident_breaks_ties(tmp_path: Path, text: str, expected: str | None) -> None:
    akas = [*AKAS, "tt2562232\t1\tBirdman\tUS\t\\N\timdbDisplay\t\\N\t0"]
    build(tmp_path / "imdb.db", lambda *a: None, base_url="https://imdb.test",
          transport=_transport([*BASICS, *SAME_NAME], akas=akas))
    hit, reason = ImdbDataset(tmp_path / "imdb.db").confident(text)
    assert (hit.imdb_id if hit else None) == expected, reason
