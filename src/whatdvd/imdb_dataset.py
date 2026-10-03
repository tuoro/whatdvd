"""IMDb 官方数据集（datasets.imdbws.com）：按 IMDb 编号查片名、原名和年份。

PTP 2.1.1 要求文件夹名和 IMDb 的原名或国际英文名一致；IMDb 没有免费的在线 API，官方提供的数据集
允许个人非商业使用，每天更新。下载时边解压边导入本地 SQLite，不保存压缩包：

- title.basics（约 230 MB）：类型、primaryTitle（IMDb 显示的名字）、originalTitle、年份。
  只保留电影、电视电影、剧集、迷你剧、特别节目和录像，不要单集（数量最多，DVD 用不到）。
- title.akas（约 520 MB）：各地区的片名。只保留英文名：国际英文名（XWW）优先，其次 US、GB，
  同一地区 imdbDisplay（IMDb 在该地区显示的名字）优先；带附注的（某个版本、剪辑版等）不要。
  IMDb 显示的名字（primaryTitle）通常已经是英文名，只有它和原名相同、而片子不是英语片时才用这些英文名。

另外建一张按片名查找的表：IMDb 显示的名字、原名，以及国际、美国、英国和俄罗斯、苏联（SUHH）地区的片名，
统一大小写、去掉标点和变音符号（ё 当作 е）。TMDB 查不到或没有 TMDB API Key 时也能按片名找到 IMDb 编号。

先导入到临时文件，完成后再替换，导入中途失败不影响正在使用的数据。
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
import zlib
from difflib import SequenceMatcher
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .release_names import guess_queries, normalize, search_variants

BASE_URL = "https://datasets.imdbws.com"
FILES = ("title.basics.tsv.gz", "title.akas.tsv.gz", "title.ratings.tsv.gz")
KEEP_TYPES = frozenset({"movie", "tvMovie", "tvSeries", "tvMiniSeries", "tvSpecial", "video"})
# 地区 → 优先级（小的优先）；同一地区 imdbDisplay 比没写类型的优先
_REGION_RANK = {"XWW": 0, "US": 2, "GB": 4}
_TYPE_RANK = {"imdbDisplay": 0, "\\N": 1}
_BATCH = 20_000
# 按片名查找时收录这些地区的片名（俄语站点的种子标题常只有俄文名）
_NAME_REGIONS = frozenset({"XWW", "US", "GB", "RU", "SUHH"})
# 同名时的类型优先级
_TYPE_ORDER = {"movie": 0, "tvMovie": 1, "video": 2, "tvMiniSeries": 3, "tvSeries": 3, "tvSpecial": 4}

_SERIES_TYPES = frozenset({"tvSeries", "tvMiniSeries"})
_SEASON = re.compile(r"\[\s*S\d|\bS\d{1,2}(?:E\d+)?\b|\bseason\b|сезон|\[\d+\s*-\s*\d+\s+из\s+\d+\]|\[\d+(?:-\d+)?\s*[xхXХ]\s*\d+", re.IGNORECASE)
"""标题里的季、集标记："[S07]"、"Season 2"、"2 сезон"、"[01-12 из 12]"、"[01-03Х01-26]"（季×集）。"""
_PACK = re.compile(
    r"(?<![А-Яа-яЁё])(?:дилогия|трилогия|тетралогия|квадрология|квадрилогия|пенталогия|гексалогия|антология|коллекция|сборник)(?![А-Яа-яЁё])|"
    r"\b(?:duology|trilogy|quadrilogy|quadrology|anthology|box\s*set)\b|\(\s*\d+\s*в\s*1\s*\)|\d+\s+фильм\w*\s+из\s+\d+",
    re.IGNORECASE,
)
"""合集：几部片放在一起的，没有唯一的 IMDb 条目（"Collection" 不算：The Collection (2012) 是一部片）。"""
_CANDIDATES = 40
_MIN_VOTES = 100
_VOTES_RATIO = 10
"""投票人数至少这么多、且是第二名的这么多倍，才按投票人数选。"""
_INSERT_TITLE = "INSERT OR REPLACE INTO titles (id, type, primary_title, original_title, year, end_year) VALUES (?, ?, ?, ?, ?, ?)"

Progress = Callable[[str, int, int], None]
"""progress(阶段说明, 已下载字节, 总字节)。"""


class DatasetError(RuntimeError):
    pass


class Cancelled(DatasetError):
    pass


@dataclass(frozen=True)
class ImdbTitle:
    imdb_id: str
    kind: str
    title: str
    """英文名：IMDb 显示的名字；非英语片显示的是原名时，换成国际英文名（XWW）或 US、GB 的片名。"""
    primary_title: str
    original_title: str
    year: int | None
    end_year: int | None = None
    """剧集的结束年份（旧版本导入的数据集没有）。"""
    votes: int = 0
    """IMDb 上的投票人数（旧版本导入的数据集没有，为 0）。"""


def _int(value: str) -> int | None:
    return int(value) if value.isdigit() else None




class ImdbDataset:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection | None:
        if not self.path.is_file():
            return None
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)

    def info(self) -> dict[str, Any] | None:
        """已导入的数据：导入时间、数据集的更新日期、片数。没有导入过时为 None。"""
        db = self._connect()
        if db is None:
            return None
        try:
            meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
        except sqlite3.Error:
            return None
        finally:
            db.close()
        return {
            "imported_at": float(meta.get("imported_at", 0)) or None,
            "source_date": meta.get("source_date"),
            "titles": int(meta.get("titles", 0)),
            "english_titles": int(meta.get("english_titles", 0)),
            "searchable": "names_version" in meta,  # 旧版本导入的没有按片名查找的表，需要更新一次
            "bytes": self.path.stat().st_size,
        }

    def search(self, query: str, year: int | None = None, limit: int = 8) -> list[ImdbTitle]:
        """按片名查找（各地区的名字都算），年份相差一年以内的排在前面，其次电影优先。"""
        key = normalize(query)
        db = self._connect()
        if db is None or not key:
            return []
        try:
            columns = {row[1] for row in db.execute("PRAGMA table_info(titles)")}
            extra = "t.end_year, t.votes" if "votes" in columns else "NULL, 0"  # 旧版本导入的没有这两列
            rows = db.execute(
                f"SELECT DISTINCT t.id, t.type, t.primary_title, t.original_title, t.year, {extra} FROM names n "
                "JOIN titles t ON t.id = n.id WHERE n.name = ? LIMIT 200",
                (key,),
            ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            db.close()

        def rank(row: tuple[Any, ...]) -> tuple[int, int, int]:
            found_year = row[4]
            distance = abs(found_year - year) if year and found_year else 99
            return (0 if distance <= 1 else 1, _TYPE_ORDER.get(row[1], 9), distance)

        rows.sort(key=rank)
        return [
            ImdbTitle(imdb_id=f"tt{number:07d}", kind=kind, title=primary, primary_title=primary,
                      original_title=original or primary, year=found_year, end_year=end_year, votes=votes or 0)
            for number, kind, primary, original, found_year, end_year, votes in rows[:limit]
        ]

    def find(self, query: str, year: int | None = None, limit: int = 8) -> list[ImdbTitle]:
        """依次尝试几种写法（原样、俄文转写、去掉副标题），合并结果；年份对得上的排在前面。"""
        found: dict[str, ImdbTitle] = {}
        # 俄文名原样和转写都查：原样只能对上 IMDb 里的俄文别名，很多苏联片只有转写名
        # （"Жаворонок" 原样对上的是匈牙利片 Pacsirta 的俄文别名，转写才找到苏联的 Zhavoronok）
        always = 2 if re.search(r"[А-Яа-яЁё]", query) else 1
        for index, variant in enumerate(search_variants(query)):
            for hit in self.search(variant, year, limit):
                found.setdefault(hit.imdb_id, hit)
            if index + 1 >= always and any(year and h.year and abs(h.year - year) <= 1 for h in found.values()):
                break  # 已经有年份对得上的，不再放宽
        hits = list(found.values())
        hits.sort(key=lambda h: 0 if year and h.year and abs(h.year - year) <= 1 else 1)
        return hits[:limit]

    def confident(self, text: str) -> tuple[ImdbTitle | None, str]:
        """自动选片名：按种子标题或文件夹名猜搜索词，只有唯一一个年份对得上（相差一年以内）的结果才采用。
        返回（结果, 说明）；没有把握时结果为 None，说明里写原因。"""
        if _PACK.search(text):
            return None, "标题像是合集（三部曲、合辑等），不自动选片名"
        queries, year = guess_queries(text)
        # 按季发布的剧集（"Game of Thrones [S07] (2017)"）：标题里是这一季的年份，IMDb 记的是开播年份
        series = bool(_SEASON.search(text))

        def matches(h: ImdbTitle) -> bool:
            if not (h.year and year):
                return False
            if abs(h.year - year) <= 1:
                return True
            return series and h.kind in _SERIES_TYPES and h.year <= year <= (h.end_year or year) + 1

        # 标题里有好几个名字（"Одинокий воин / Mercenary / El guerrero sin nombre"）：每个都查，
        # 优先用能直接对上片名本身（不是靠别名）、年份又完全一致的名字，其次是有年份对得上的第一个名字。
        # 多取一些结果：同名的条目很多时（Supernatural），开播年份离得远的剧集会排到后面
        def own_name(h: ImdbTitle, name: str) -> bool:
            """片名本身就是这个名字（不是靠别名对上的）。俄文名转写允许细小差别：IMDb 写 "Sedmoye nebo"，
            转写出来是 "sedmoe nebo"。"""
            ours = {normalize(v) for v in search_variants(name)}
            theirs = {normalize(h.primary_title), normalize(h.original_title)}
            return bool(ours & theirs) or any(
                len(a) >= 5 and SequenceMatcher(None, a, b).ratio() >= 0.88 for a in ours for b in theirs
            )

        tried = [(name, self.find(name, year, _CANDIDATES)) for name in (queries if year else queries[:1])]

        def rank(item: tuple[str, list[ImdbTitle]]) -> int:
            name, found = item
            good_hits = [h for h in found if matches(h)]
            if any(h.year == year and own_name(h, name) for h in good_hits):
                return 0
            return 1 if good_hits else 2

        query, hits = min(tried, key=rank)  # 同等的按标题里的顺序
        if year is None:
            if len(hits) == 1:
                return hits[0], f"按“{query}”找到唯一一个结果"
            return None, f"“{query}”没有年份，找到 {len(hits)} 个结果，无法确定"
        good = [h for h in hits if matches(h)]
        # 有季、集标记的只要剧集（"Клетка [01-04 из 04] (2001)" 不是俄文名也叫“Клетка”的电影 The Cell）
        if series and good:
            good = [h for h in good if h.kind in _SERIES_TYPES or h.kind == "tvMovie"]
            if not good:
                return None, f"标题是剧集，IMDb 数据集中没有对得上的“{query}”（{year}）剧集"
        # 只有俄文名时，片名本身就是这个名字（转写）的优先，不要俄文译名恰好相同的外国片
        # （"Жаворонок (1964)" 是苏联的 Zhavoronok，不是匈牙利的 Pacsirta；"Агент (2013)" 不是美剧 Turn）
        if re.search(r"[А-Яа-яЁё]", query) and not re.search(r"[A-Za-z]", query):
            own = [h for h in good if own_name(h, query)]
            if own and len(own) < len(good):
                good = own
        names = {normalize(v) for v in search_variants(query)}

        def overshadowed(chosen: ImdbTitle) -> ImdbTitle | None:
            """年份差几年、但有名得多的同名片（You're Next：电影 IMDb 记 2011 年，DVD 标 2013 年，
            年份只对得上一部 2014 年的同名剧集）：这时选中的多半不对。"""
            if chosen.year == year:  # 年份完全一致的照常采用（Vice (2015) 不因为 2018 年那部更有名就不选）
                return None
            for other in hits:
                if (other is not chosen and other.year and abs(other.year - year) <= 3
                        and other.votes >= _MIN_VOTES and other.votes >= _VOTES_RATIO * max(chosen.votes, 1)
                        and names & {normalize(other.primary_title), normalize(other.original_title)}):
                    return other
            return None

        def accept(chosen: ImdbTitle, how: str) -> tuple[ImdbTitle | None, str]:
            if (other := overshadowed(chosen)) is not None:
                return None, f"“{query}”（{year}）对得上的是 {chosen.title}（{chosen.year}），但 {other.year} 年有更有名的同名片，无法确定"
            return chosen, how

        if len(good) == 1:
            return accept(good[0], f"按“{query}”和年份 {year} 找到唯一一个结果")
        if not good:
            return None, f"IMDb 数据集中没有“{query}”（{year}）"
        # 同名同年的有好几个：依次只留投票人数远多于其他的、年份完全一致的、片名本身就是这个名字的
        # （不是靠别名对上的）、电影，只剩一个才采用。例如 Her (2013) 的电影和同名 video

        def most_voted(items: list[ImdbTitle]) -> list[ImdbTitle]:
            ranked = sorted(items, key=lambda h: h.votes, reverse=True)
            top, second = ranked[0].votes, ranked[1].votes
            return ranked[:1] if top >= _MIN_VOTES and top >= _VOTES_RATIO * second else items

        by_year: tuple[str, Callable[[list[ImdbTitle]], list[ImdbTitle]]] = (
            "年份完全一致", lambda items: [h for h in items if h.year == year])
        by_votes: tuple[str, Callable[[list[ImdbTitle]], list[ImdbTitle]]] = ("投票人数", most_voted)
        by_name: tuple[str, Callable[[list[ImdbTitle]], list[ImdbTitle]]] = (
            "片名相同", lambda items: [h for h in items if own_name(h, query)])
        not_series: tuple[str, Callable[[list[ImdbTitle]], list[ImdbTitle]]] = (
            "不是剧集", lambda items: [h for h in items if h.kind != "tvSeries"])
        by_movie: tuple[str, Callable[[list[ImdbTitle]], list[ImdbTitle]]] = (
            "是电影", lambda items: [h for h in items if h.kind == "movie"])
        if series:
            # 按季发布的剧集先看投票人数：真正的条目是开播年份的那个（Supernatural）
            steps = [by_votes, by_year, by_name, by_movie]
        else:
            # 电影：先不要剧集（"Уик-энд / Weekend (2014)" 是 2013 年的电影），再看片名本身对不对得上
            # （"Reservoir Dogs [1991]" 是 IMDb 记 1992 年的电影，不是 1991 年的同名录像；"Desire 1996" 是 Désiré，
            # 不是别名叫 Desire 的印度片），然后才看年份、投票人数
            steps = [not_series, by_name, by_year, by_votes, by_movie]
        used: list[str] = []
        for label, narrow in steps:
            narrowed = narrow(good)
            if label == "片名相同" and any(h.kind == "movie" for h in good) and not any(h.kind == "movie" for h in narrowed):
                # 靠别名对上的电影和同名的 video 等：Birdman (2014) 该选电影，Saving Santa (2013) 该选 video，
                # 而两部电影的票数都比同名 video 多得多，分不出来，不选
                break
            if narrowed and len(narrowed) < len(good):
                good, used = narrowed, [*used, label]
            if len(good) == 1:
                return accept(good[0], f"按“{query}”和年份 {year} 找到 {len(hits)} 个结果，按{'、'.join(used)}选出一个")
        return None, f"“{query}”（{year}）有 {len(good)} 个同名同年的结果，无法确定"

    def title(self, imdb_id: str, original_language: str = "") -> ImdbTitle | None:
        """查不到或还没导入时为 None。original_language 来自 TMDB（数据集中没有语言）。"""
        number = _int(imdb_id.removeprefix("tt"))
        db = self._connect()
        if db is None or number is None:
            return None
        try:
            row = db.execute(
                "SELECT t.type, t.primary_title, t.original_title, t.year, e.title FROM titles t "
                "LEFT JOIN english e ON e.id = t.id WHERE t.id = ?",
                (number,),
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            db.close()
        if row is None:
            return None
        kind, primary, original, year, english = row
        foreign_shown_as_original = original_language not in ("", "en") and primary == (original or primary)
        return ImdbTitle(
            imdb_id=imdb_id, kind=kind, title=(english or primary) if foreign_shown_as_original else primary,
            primary_title=primary,
            original_title=original or primary, year=year,
        )


def _lines(
    client: httpx.Client, url: str, phase: str, progress: Progress, stop: threading.Event
) -> Iterator[list[str]]:
    """边下载边解压，逐行给出按 Tab 分开的字段（跳过表头）。"""
    with client.stream("GET", url) as response:
        if not response.is_success:
            raise DatasetError(f"下载 {url} 失败：HTTP {response.status_code}")
        total = int(response.headers.get("content-length") or 0)
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        pending = b""
        done = 0
        header = True
        last_report = 0.0
        for chunk in response.iter_raw():
            if stop.is_set():
                raise Cancelled("已取消")
            done += len(chunk)
            now = time.monotonic()
            if now - last_report > 0.5:
                progress(phase, done, total)
                last_report = now
            data = pending + decompressor.decompress(chunk)
            lines = data.split(b"\n")
            pending = lines.pop()
            for line in lines:
                if header:
                    header = False
                    continue
                yield line.decode("utf-8", "replace").split("\t")
        pending += decompressor.flush()
        if pending.strip() and not header:
            yield pending.decode("utf-8", "replace").split("\t")
        progress(phase, done, total)


def _source_date(client: httpx.Client, url: str) -> str | None:
    try:
        value = client.head(url).headers.get("last-modified")
    except httpx.HTTPError:
        return None
    return str(value) if value else None


def build(
    path: Path,
    progress: Progress,
    *,
    stop: threading.Event | None = None,
    base_url: str = BASE_URL,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """下载并导入数据集，完成后替换 path。返回导入的统计。"""
    stop = stop or threading.Event()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".importing")
    tmp.unlink(missing_ok=True)
    client = httpx.Client(timeout=httpx.Timeout(60.0, read=120.0), transport=transport, follow_redirects=True,
                          headers={"User-Agent": "whatdvd (https://github.com/tuoro/whatdvd)"})
    db = sqlite3.connect(tmp)
    try:
        db.executescript(
            """
            PRAGMA journal_mode = OFF;
            PRAGMA synchronous = OFF;
            CREATE TABLE titles (id INTEGER PRIMARY KEY, type TEXT, primary_title TEXT, original_title TEXT,
                                 year INTEGER, end_year INTEGER, votes INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE akas (id INTEGER, rank INTEGER, title TEXT);
            CREATE TABLE all_names (name TEXT, id INTEGER);
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        basics_url, akas_url, ratings_url = (f"{base_url}/{name}" for name in FILES)
        source_date = _source_date(client, basics_url)

        batch: list[tuple[Any, ...]] = []
        names: list[tuple[str, int]] = []

        def flush_names() -> None:
            db.executemany("INSERT INTO all_names VALUES (?, ?)", names)
            names.clear()

        for fields in _lines(client, basics_url, "下载并导入 title.basics（1/3）", progress, stop):
            if len(fields) >= 6 and fields[1] in KEEP_TYPES:
                number = int(fields[0][2:])
                original = fields[3] if fields[3] != "\\N" else None
                end_year = _int(fields[6]) if len(fields) > 6 else None
                batch.append((number, fields[1], fields[2], original, _int(fields[5]), end_year))
                names.append((normalize(fields[2]), number))
                if original and original != fields[2]:
                    names.append((normalize(original), number))
                if len(batch) >= _BATCH:
                    db.executemany(_INSERT_TITLE, batch)
                    batch.clear()
                    flush_names()
        db.executemany(_INSERT_TITLE, batch)
        batch.clear()
        flush_names()

        for fields in _lines(client, akas_url, "下载并导入 title.akas（2/3）", progress, stop):
            if len(fields) < 7:
                continue
            if fields[3] in _NAME_REGIONS:  # 按片名查找：这些地区的名字都收录（单集的在最后去掉）
                names.append((normalize(fields[2]), int(fields[0][2:])))
                if len(names) >= _BATCH:
                    flush_names()
            if fields[6] != "\\N":  # 英文名：带附注的（某个版本、剪辑版、直译名……）不要
                continue
            region_rank = _REGION_RANK.get(fields[3])
            type_rank = _TYPE_RANK.get(fields[5])
            if region_rank is None or type_rank is None:
                continue
            batch.append((int(fields[0][2:]), region_rank + type_rank, fields[2]))
            if len(batch) >= _BATCH:
                db.executemany("INSERT INTO akas VALUES (?, ?, ?)", batch)
                batch.clear()
        db.executemany("INSERT INTO akas VALUES (?, ?, ?)", batch)
        batch.clear()
        flush_names()

        # 投票人数：同名同年的好几个结果里，正片通常比同名的短片、录像多出几个数量级
        votes: list[tuple[int, int]] = []
        for fields in _lines(client, ratings_url, "下载并导入 title.ratings（3/3）", progress, stop):
            if len(fields) >= 3 and fields[0].startswith("tt") and fields[2].isdigit():
                votes.append((int(fields[2]), int(fields[0][2:])))
                if len(votes) >= _BATCH:
                    db.executemany("UPDATE titles SET votes = ? WHERE id = ?", votes)
                    votes.clear()
        db.executemany("UPDATE titles SET votes = ? WHERE id = ?", votes)

        progress("整理数据", 0, 0)
        db.executescript(
            """
            CREATE TABLE english (id INTEGER PRIMARY KEY, title TEXT);
            INSERT INTO english
              SELECT id, title FROM (
                SELECT a.id, a.title, ROW_NUMBER() OVER (PARTITION BY a.id ORDER BY a.rank) AS n
                FROM akas a JOIN titles t ON t.id = a.id
              ) WHERE n = 1;
            DROP TABLE akas;
            CREATE TABLE names (name TEXT, id INTEGER, PRIMARY KEY (name, id)) WITHOUT ROWID;
            INSERT OR IGNORE INTO names
              SELECT a.name, a.id FROM all_names a JOIN titles t ON t.id = a.id WHERE a.name != '';
            DROP TABLE all_names;
            """
        )
        titles = db.execute("SELECT COUNT(*) FROM titles").fetchone()[0]
        english = db.execute("SELECT COUNT(*) FROM english").fetchone()[0]
        if titles == 0:
            raise DatasetError("数据集中没有读到任何片名，可能是格式变了")
        stats = {"imported_at": time.time(), "source_date": source_date or "", "titles": titles,
                 "english_titles": english, "names_version": 2}
        db.executemany("INSERT INTO meta VALUES (?, ?)", [(k, str(v)) for k, v in stats.items()])
        db.commit()
        db.execute("VACUUM")
        db.close()
        os.replace(tmp, path)
        return stats
    except BaseException:
        db.close()
        tmp.unlink(missing_ok=True)
        raise
    finally:
        client.close()
