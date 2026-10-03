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
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .release_names import guess_query, normalize, search_variants

BASE_URL = "https://datasets.imdbws.com"
FILES = ("title.basics.tsv.gz", "title.akas.tsv.gz")
KEEP_TYPES = frozenset({"movie", "tvMovie", "tvSeries", "tvMiniSeries", "tvSpecial", "video"})
# 地区 → 优先级（小的优先）；同一地区 imdbDisplay 比没写类型的优先
_REGION_RANK = {"XWW": 0, "US": 2, "GB": 4}
_TYPE_RANK = {"imdbDisplay": 0, "\\N": 1}
_BATCH = 20_000
# 按片名查找时收录这些地区的片名（俄语站点的种子标题常只有俄文名）
_NAME_REGIONS = frozenset({"XWW", "US", "GB", "RU", "SUHH"})
# 同名时的类型优先级
_TYPE_ORDER = {"movie": 0, "tvMovie": 1, "video": 2, "tvMiniSeries": 3, "tvSeries": 3, "tvSpecial": 4}

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
            rows = db.execute(
                "SELECT DISTINCT t.id, t.type, t.primary_title, t.original_title, t.year, e.title FROM names n "
                "JOIN titles t ON t.id = n.id LEFT JOIN english e ON e.id = t.id WHERE n.name = ? LIMIT 200",
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
                      original_title=original or primary, year=found_year)
            for number, kind, primary, original, found_year, _ in rows[:limit]
        ]

    def find(self, query: str, year: int | None = None, limit: int = 8) -> list[ImdbTitle]:
        """依次尝试几种写法（原样、俄文转写、去掉副标题），合并结果；年份对得上的排在前面。"""
        found: dict[str, ImdbTitle] = {}
        for variant in search_variants(query):
            for hit in self.search(variant, year, limit):
                found.setdefault(hit.imdb_id, hit)
            if any(year and h.year and abs(h.year - year) <= 1 for h in found.values()):
                break  # 已经有年份对得上的，不再放宽
        hits = list(found.values())
        hits.sort(key=lambda h: 0 if year and h.year and abs(h.year - year) <= 1 else 1)
        return hits[:limit]

    def confident(self, text: str) -> tuple[ImdbTitle | None, str]:
        """自动选片名：按种子标题或文件夹名猜搜索词，只有唯一一个年份对得上（相差一年以内）的结果才采用。
        返回（结果, 说明）；没有把握时结果为 None，说明里写原因。"""
        query, year = guess_query(text)
        hits = self.find(query, year)
        if year is None:
            if len(hits) == 1:
                return hits[0], f"按“{query}”找到唯一一个结果"
            return None, f"“{query}”没有年份，找到 {len(hits)} 个结果，无法确定"
        good = [h for h in hits if h.year and abs(h.year - year) <= 1]
        if len(good) == 1:
            return good[0], f"按“{query}”和年份 {year} 找到唯一一个结果"
        if not good:
            return None, f"IMDb 数据集中没有“{query}”（{year}）"
        # 同名同年的有好几个：依次只留年份完全一致的、片名本身就是这个名字的（不是靠别名对上的）、电影，
        # 只剩一个才采用。2026 年 10 月 rutor 上 600 个 DVD 标题中这样的 54 个，例如 Her (2013) 的电影和同名 video
        names = {normalize(v) for v in search_variants(query)}
        steps: list[tuple[str, Callable[[ImdbTitle], bool]]] = [
            ("年份完全一致", lambda h: h.year == year),
            ("片名相同", lambda h: bool(names & {normalize(h.primary_title), normalize(h.original_title)})),
            ("是电影", lambda h: h.kind == "movie"),
        ]
        used: list[str] = []
        for label, keep in steps:
            narrowed = [h for h in good if keep(h)]
            if label == "片名相同" and any(h.kind == "movie" for h in good) and not any(h.kind == "movie" for h in narrowed):
                # 靠别名对上的电影和同名的 video 等：Birdman (2014) 该选电影，Saving Santa (2013) 该选 video，分不出来
                break
            if narrowed and len(narrowed) < len(good):
                good, used = narrowed, [*used, label]
            if len(good) == 1:
                return good[0], f"按“{query}”和年份 {year} 找到 {len(hits)} 个结果，按{'、'.join(used)}选出一个"
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
                                 year INTEGER);
            CREATE TABLE akas (id INTEGER, rank INTEGER, title TEXT);
            CREATE TABLE all_names (name TEXT, id INTEGER);
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        basics_url, akas_url = (f"{base_url}/{name}" for name in FILES)
        source_date = _source_date(client, basics_url)

        batch: list[tuple[Any, ...]] = []
        names: list[tuple[str, int]] = []

        def flush_names() -> None:
            db.executemany("INSERT INTO all_names VALUES (?, ?)", names)
            names.clear()

        for fields in _lines(client, basics_url, "下载并导入 title.basics（1/2）", progress, stop):
            if len(fields) >= 6 and fields[1] in KEEP_TYPES:
                number = int(fields[0][2:])
                original = fields[3] if fields[3] != "\\N" else None
                batch.append((number, fields[1], fields[2], original, _int(fields[5])))
                names.append((normalize(fields[2]), number))
                if original and original != fields[2]:
                    names.append((normalize(original), number))
                if len(batch) >= _BATCH:
                    db.executemany("INSERT OR REPLACE INTO titles VALUES (?, ?, ?, ?, ?)", batch)
                    batch.clear()
                    flush_names()
        db.executemany("INSERT OR REPLACE INTO titles VALUES (?, ?, ?, ?, ?)", batch)
        batch.clear()
        flush_names()

        for fields in _lines(client, akas_url, "下载并导入 title.akas（2/2）", progress, stop):
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
        flush_names()

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
                 "english_titles": english, "names_version": 1}
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
