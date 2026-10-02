"""发种目录的硬链接：同一份数据、可以改名、原始下载不受影响。"""

import os
from pathlib import Path

import pytest

from whatdvd.seedlink import LinkError, check_name, link_tree, target_name


def _disc(root: Path) -> Path:
    disc = root / "Изумрудный лес (1985) DVD9"
    (disc / "VIDEO_TS").mkdir(parents=True)
    (disc / "VIDEO_TS" / "VTS_01_1.VOB").write_bytes(b"vob")
    (disc / "VIDEO_TS" / "VTS_01_0.IFO").write_bytes(b"ifo")
    (disc / "AUDIO_TS").mkdir()
    (disc / "info.nfo").write_text("nfo")
    return disc


def test_link_tree_with_new_name(tmp_path: Path) -> None:
    disc = _disc(tmp_path / "downloads")
    target, created = link_tree(disc, tmp_path / "seed", "The.Emerald.Forest.1985.DVD9")
    assert created and target == tmp_path / "seed" / "The.Emerald.Forest.1985.DVD9"
    vob = target / "VIDEO_TS" / "VTS_01_1.VOB"
    assert vob.read_bytes() == b"vob" and os.path.samefile(vob, disc / "VIDEO_TS" / "VTS_01_1.VOB")
    assert (target / "info.nfo").exists()
    assert disc.name == "Изумрудный лес (1985) DVD9"  # 原始下载不动
    assert [p.name for p in (tmp_path / "seed").iterdir()] == [target.name]  # 没有留下临时目录

    (disc / "VIDEO_TS" / "VTS_01_1.VOB").unlink()  # 原始下载删除后，发种目录中的仍在
    assert vob.read_bytes() == b"vob"


def test_link_tree_keeps_original_name_by_default(tmp_path: Path) -> None:
    disc = _disc(tmp_path / "downloads")
    target, _ = link_tree(disc, tmp_path / "seed")
    assert target.name == disc.name


def test_link_tree_reuses_same_data_and_refuses_different(tmp_path: Path) -> None:
    disc = _disc(tmp_path / "downloads")
    first, created = link_tree(disc, tmp_path / "seed", "Film")
    again, created_again = link_tree(disc, tmp_path / "seed", "Film")
    assert created and not created_again and again == first

    other = tmp_path / "other"
    (other / "VIDEO_TS").mkdir(parents=True)
    (other / "VIDEO_TS" / "VTS_01_1.VOB").write_bytes(b"different")
    with pytest.raises(LinkError, match="不是同一份数据"):
        link_tree(other, tmp_path / "seed", "Film")


def test_link_iso_adds_extension(tmp_path: Path) -> None:
    iso = tmp_path / "downloads" / "Фильм.iso"
    iso.parent.mkdir()
    iso.write_bytes(b"iso")
    target, _ = link_tree(iso, tmp_path / "seed", "Film 1985")
    assert target.name == "Film 1985.iso" and os.path.samefile(target, iso)
    assert target_name(iso, "Film.ISO") == "Film.ISO"


def test_different_filesystem_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    disc = _disc(tmp_path / "downloads")
    monkeypatch.setattr("whatdvd.seedlink.same_filesystem", lambda a, b: False)
    with pytest.raises(LinkError, match="不在同一个文件系统"):
        link_tree(disc, tmp_path / "seed")
    assert not any((tmp_path / "seed").iterdir())


def test_link_failure_leaves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    disc = _disc(tmp_path / "downloads")
    calls = 0

    def flaky(src: Path, dst: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError(13, "Permission denied")
        os.link(src, dst)

    monkeypatch.setattr("whatdvd.seedlink.os.link", flaky)
    with pytest.raises(LinkError, match="建立硬链接失败"):
        link_tree(disc, tmp_path / "seed", "Film")
    assert not any((tmp_path / "seed").iterdir())


@pytest.mark.parametrize("name", ["", "  ", ".", "..", "a/b", "a\\b", ".hidden", "x" * 256])
def test_bad_names(name: str) -> None:
    with pytest.raises(LinkError):
        check_name(name)


def test_separate_mounts_of_same_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker 中同一块盘分开挂载（/media、/seed）：设备号相同，但不能跨挂载点建硬链接。"""
    from whatdvd.seedlink import same_filesystem

    (tmp_path / "media").mkdir()
    (tmp_path / "seed").mkdir()
    assert same_filesystem(tmp_path / "media", tmp_path / "seed")
    mounts = {tmp_path / "media", tmp_path / "seed"}
    real = os.path.ismount
    monkeypatch.setattr("whatdvd.seedlink.os.path.ismount", lambda p: Path(p) in mounts or real(p))
    assert not same_filesystem(tmp_path / "media", tmp_path / "seed")
