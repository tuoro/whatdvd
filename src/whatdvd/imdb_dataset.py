"""IMDb 官方数据集（datasets.imdbws.com）：按 IMDb 编号查片名、原名和年份。

PTP 2.1.1 要求文件夹名和 IMDb 的原名或国际英文名一致；IMDb 没有免费的在线 API，官方提供的数据集
允许个人非商业使用，每天更新。下载时边解压边导入本地 SQLite，不保存压缩包：

- title.basics（约 230 MB）：类型、primaryTitle（IMDb 显示的名字）、originalTitle、年份。
  只保留电影、电视电影、剧集、迷你剧、特别节目和录像，不要单集（数量最多，DVD 用不到）。
- title.akas（约 520 MB）：各地区的片名。只保留英文名：国际英文名（XWW）优先，其次 US、GB，
  同一地区 imdbDisplay（IMDb 在该地区显示的名字）优先；带附注的（某个版本、剪辑版等）不要。
  IMDb 显示的名字（primaryTitle）通常已经是英文名，只有它和原名相同、而片子不是英语片时才用这些英文名。

先导入到临时文件，完成后再替换，导入中途失败不影响正在使用的数据。
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://datasets.imdbws.com"
FILES = ("title.basics.tsv.gz", "title.akas.tsv.gz")
KEEP_TYPES = frozenset({"movie", "tvMovie", "tvSeries", "tvMiniSeries", "tvSpecial", "video"})
# 地区 → 优先级（小的优先）；同一地区 imdbDisplay 比没写类型的优先
_REGION_RANK = {"XWW": 0, "US": 2, "GB": 4}
_TYPE_RANK = {"imdbDisplay": 0, "\\N": 1}
_BATCH = 20_000

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
            "bytes": self.path.stat().st_size,
        }

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
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        basics_url, akas_url = (f"{base_url}/{name}" for name in FILES)
        source_date = _source_date(client, basics_url)

        batch: list[tuple[Any, ...]] = []
        for fields in _lines(client, basics_url, "下载并导入 title.basics（1/2）", progress, stop):
            if len(fields) >= 6 and fields[1] in KEEP_TYPES:
                original = fields[3] if fields[3] != "\\N" else None
                batch.append((int(fields[0][2:]), fields[1], fields[2], original, _int(fields[5])))
                if len(batch) >= _BATCH:
                    db.executemany("INSERT OR REPLACE INTO titles VALUES (?, ?, ?, ?, ?)", batch)
                    batch.clear()
        db.executemany("INSERT OR REPLACE INTO titles VALUES (?, ?, ?, ?, ?)", batch)
        batch.clear()

        for fields in _lines(client, akas_url, "下载并导入 title.akas（2/2）", progress, stop):
            if len(fields) < 7 or fields[6] != "\\N":  # 带附注的（某个版本、剪辑版、工作名……）不要
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
            """
        )
        titles = db.execute("SELECT COUNT(*) FROM titles").fetchone()[0]
        english = db.execute("SELECT COUNT(*) FROM english").fetchone()[0]
        if titles == 0:
            raise DatasetError("数据集中没有读到任何片名，可能是格式变了")
        stats = {"imported_at": time.time(), "source_date": source_date or "", "titles": titles,
                 "english_titles": english}
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
