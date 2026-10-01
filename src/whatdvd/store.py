"""资源候选与下载状态，保存在 SQLite 中，服务重启后保留。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Literal

Status = Literal["new", "ignored", "sent", "downloading", "processing", "done", "failed"]
STATUSES: tuple[Status, ...] = ("new", "ignored", "sent", "downloading", "processing", "done", "failed")
"""new 候选；ignored 已忽略；sent 已推送到 qB；downloading 下载中；processing 处理中；done 处理完成；failed 失败。"""


@dataclass(frozen=True)
class Record:
    id: str
    title: str
    source: str
    """来源：Jackett 中的站点名，或 "qBittorrent"（在 qB 中手动添加到分类的种子）。"""
    status: Status = "new"
    size: int = 0
    published: float | None = None
    details_url: str | None = None
    download_url: str | None = None
    """Jackett 的下载地址，含 API key，不发给浏览器。"""
    magnet: str | None = None
    info_hash: str | None = None
    seeders: int | None = None
    kind: str | None = None
    """DVD5 / DVD9。"""
    discs: int = 1
    marker: str | None = None
    """untouched：标题标明原盘；None：未标明。"""
    warnings: list[str] = field(default_factory=list)
    progress: float = 0.0
    local_path: str | None = None
    job_id: str | None = None
    post_file: str | None = None
    output_dir: str | None = None
    error: str | None = None
    found_at: float = 0.0
    updated_at: float = 0.0

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        del data["download_url"]
        return data


_COLUMNS = [f.name for f in fields(Record)]
_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS records (
    {", ".join(f"{name} TEXT" if name != "id" else "id TEXT PRIMARY KEY" for name in _COLUMNS)}
);
CREATE INDEX IF NOT EXISTS records_hash ON records (info_hash);
CREATE INDEX IF NOT EXISTS records_status ON records (status);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _encode(record: Record) -> list[Any]:
        return [json.dumps(getattr(record, name), ensure_ascii=False) for name in _COLUMNS]

    @staticmethod
    def _decode(row: Iterable[Any]) -> Record:
        return Record(**{name: json.loads(value) for name, value in zip(_COLUMNS, row, strict=True)})

    def _query(self, sql: str, args: Iterable[Any] = ()) -> list[Record]:
        with self._lock:
            rows = self._db.execute(sql, list(args)).fetchall()
        return [self._decode(row) for row in rows]

    def save(self, record: Record) -> Record:
        now = time.time()
        record = replace(record, updated_at=now, found_at=record.found_at or now)
        placeholders = ", ".join("?" for _ in _COLUMNS)
        with self._lock:
            self._db.execute(f"INSERT OR REPLACE INTO records ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
                             self._encode(record))
        return record

    def add_new(self, record: Record) -> bool:
        """只在 id 和 info hash 都没出现过时写入，返回是否新增。"""
        if self.get(record.id) is not None or (record.info_hash and self.by_hash(record.info_hash)):
            return False
        self.save(record)
        return True

    def update(self, record_id: str, **changes: Any) -> Record:
        record = self.get(record_id)
        if record is None:
            raise KeyError(record_id)
        return self.save(replace(record, **changes))

    def get(self, record_id: str) -> Record | None:
        found = self._query(f"SELECT {', '.join(_COLUMNS)} FROM records WHERE id = ?", [json.dumps(record_id)])
        return found[0] if found else None

    def by_hash(self, info_hash: str) -> Record | None:
        found = self._query(
            f"SELECT {', '.join(_COLUMNS)} FROM records WHERE info_hash = ?", [json.dumps(info_hash.lower())]
        )
        return found[0] if found else None

    def list(self, statuses: Iterable[Status] = STATUSES) -> list[Record]:
        wanted = [json.dumps(status) for status in statuses]
        if not wanted:
            return []
        marks = ", ".join("?" for _ in wanted)
        records = self._query(f"SELECT {', '.join(_COLUMNS)} FROM records WHERE status IN ({marks})", wanted)
        return sorted(records, key=lambda r: r.published if r.published is not None else r.found_at, reverse=True)
