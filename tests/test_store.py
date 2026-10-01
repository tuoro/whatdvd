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
