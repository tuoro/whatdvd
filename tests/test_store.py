from pathlib import Path

import pytest

from whatdvd.store import Record, Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "state" / "whatdvd.db")


def test_save_and_reload(tmp_path: Path) -> None:
    path = tmp_path / "whatdvd.db"
    store = Store(path)
    store.save(Record(id="a", title="Movie [DVD9]", source="rutracker", size=8, warnings=["体积偏大"], info_hash="abc"))
    store.close()

    reopened = Store(path)  # 重启后仍在
    record = reopened.get("a")
    assert record is not None
    assert (record.title, record.size, record.warnings, record.status) == ("Movie [DVD9]", 8, ["体积偏大"], "new")
    assert record.found_at > 0 and record.updated_at >= record.found_at


def test_add_new_skips_known_id_and_hash(store: Store) -> None:
    assert store.add_new(Record(id="a", title="A", source="x", info_hash="h1"))
    assert not store.add_new(Record(id="a", title="A again", source="x"))
    assert not store.add_new(Record(id="b", title="same torrent elsewhere", source="y", info_hash="h1"))
    assert store.get("a").title == "A"  # type: ignore[union-attr]


def test_update_keeps_other_fields(store: Store) -> None:
    store.save(Record(id="a", title="A", source="x", download_url="http://jackett/dl?apikey=k"))
    updated = store.update("a", status="sent", info_hash="h")
    assert updated.status == "sent" and updated.download_url == "http://jackett/dl?apikey=k"
    assert store.by_hash("H") == updated
    with pytest.raises(KeyError):
        store.update("missing", status="done")


def test_list_by_status_newest_first(store: Store) -> None:
    store.save(Record(id="old", title="old", source="x", published=100))
    store.save(Record(id="new", title="new", source="x", published=200))
    store.save(Record(id="ignored", title="i", source="x", status="ignored", published=300))
    assert [r.id for r in store.list(["new"])] == ["new", "old"]
    assert [r.id for r in store.list()] == ["ignored", "new", "old"]
    assert store.list([]) == []


def test_public_hides_download_url() -> None:
    data = Record(id="a", title="A", source="x", download_url="http://jackett/dl?apikey=secret").public()
    assert "download_url" not in data and "secret" not in str(data)


def test_old_database_without_new_columns(tmp_path: Path) -> None:
    """旧版本建的表没有 labels 字段：打开时自动补上，旧记录读出默认值。"""
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE records (id TEXT PRIMARY KEY, title TEXT, source TEXT, status TEXT)")
    db.execute("""INSERT INTO records VALUES ('"a"', '"Old DVD9"', '"x"', '"new"')""")
    db.commit()
    db.close()

    store = Store(path)
    record = store.get("a")
    assert record is not None and record.title == "Old DVD9" and record.labels == [] and record.warnings == []
    store.save(Record(id="b", title="New", source="x", labels=["原声，没有翻译（БП）"]))
    assert store.get("b").labels == ["原声，没有翻译（БП）"]  # type: ignore[union-attr]
