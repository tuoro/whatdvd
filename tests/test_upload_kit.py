from types import SimpleNamespace

from whatdvd.mediainfo import split_report
from whatdvd.release_names import unit3d_title
from whatdvd.upload_kit import KitDisc, build_kits, unit3d_description

VOB = "General\nComplete name : Film/VIDEO_TS/VTS_01_1.VOB\n\nVideo\nFormat : MPEG Video\n"
IFO = "General\nComplete name : Film/VIDEO_TS/VTS_01_0.IFO\n\nMenu\nFormat : DVD-Video\n"
REPORT = f"{VOB}\n\n\n{IFO}"
NAMES = {"bhd": "x", "audio": "DD5.1", "imdb_id": "tt0091251", "tmdb_url": "https://www.themoviedb.org/movie/25237",
         "kind": "2xDVD9", "standard": "PAL"}
TITLE = {"title": "Come and See", "original_title": "Idi i smotri", "original_language": "ru", "year": 1985}


def site(id: str, kind: str = "unit3d", enabled: bool = True, announce: str = "") -> SimpleNamespace:
    return SimpleNamespace(id=id, name=id.title(), kind=kind, enabled=enabled, announce=announce)


def test_unit3d_title() -> None:
    assert unit3d_title(title="Come and See", original_title="Idi i smotri", original_language="ru", year=1985,
                        standard="PAL", kind="2xDVD9", audio="DD5.1") == "Come and See AKA Idi i smotri 1985 PAL 2xDVD9 DD 5.1"
    assert unit3d_title(title="Blade Runner", year=1982, standard="NTSC", kind="DVD9", audio="DTS 5.1",
                        edition="Final Cut") == "Blade Runner 1982 Final Cut NTSC DVD9 DTS 5.1"


def test_split_report() -> None:
    vob, ifo = split_report(REPORT)
    assert vob == VOB and ifo == IFO
    assert split_report(VOB) == (VOB, None)


def test_unit3d_description() -> None:
    discs = [KitDisc("Film", "VTS_01_1.VOB", REPORT, [("https://img/1.png", "https://pix/1"), ("https://img/2.png", "https://pix/2"),
                                                     ("https://img/3.png", "https://pix/3")])]
    text = unit3d_description(discs)
    assert text.startswith("[center][spoiler=VTS_01_1.VOB][code]General\nComplete name : Film/VIDEO_TS/VTS_01_1.VOB")
    assert "IFO" not in text
    assert ("[url=https://pix/1][img=350]https://img/1.png[/img][/url] [url=https://pix/2][img=350]https://img/2.png[/img][/url]\n"
            "[url=https://pix/3][img=350]https://img/3.png[/img][/url][/center]") in text
    two = unit3d_description([discs[0], KitDisc("Disc 2", "VTS_02_1.VOB", REPORT)])
    assert "[center][b]Disc 2[/b][/center]" in two and two.count("[spoiler=") == 2


def test_build_kits_fields_and_verdict() -> None:
    discs = [KitDisc("Film", "VTS_01_1.VOB", REPORT)]
    sites = [site("blutopia", announce="https://tracker/announce/secret"), site("ptp", "ptp"), site("off", enabled=False)]
    dupes = [{"site": "blutopia", "error": None, "items": []}]
    kits = build_kits(sites, NAMES, TITLE, {"region": ""}, discs, dupes)
    assert [k["site"] for k in kits] == ["blutopia", "ptp"]
    assert kits[1] == {"site": "ptp", "name": "Ptp", "kind": "ptp", "supported": False}
    kit = kits[0]
    fields = {f["label"]: f["value"] for f in kit["fields"]}
    assert fields == {"名称": "Come and See AKA Idi i smotri 1985 PAL 2xDVD9 DD 5.1", "分类": "Movie", "类型": "Full Disc",
                      "分辨率": "576i", "IMDb": "tt0091251", "TMDB": "25237"}
    assert kit["mediainfo"] == IFO.rstrip() and kit["announce_set"] is True
    assert "secret" not in str(kit)
    assert kit["verdict"] == {"level": "ok", "reasons": []}

    def verdict(items: list[dict[str, object]], **kw: object) -> dict[str, object]:
        disc = KitDisc("Film", "VTS_01_1.VOB", REPORT, warnings=list(kw.get("warnings", [])))  # type: ignore[call-overload]
        return build_kits(sites[:1], NAMES, TITLE, {}, [disc], [{"site": "blutopia", "error": None, "items": items}])[0]["verdict"]

    assert verdict([{"title": "A", "size_match": "exact", "same": True}])["level"] == "no"
    assert verdict([{"title": "A", "size_match": "near", "same": True}])["level"] == "check"
    assert verdict([{"title": "A", "size_match": None, "same": True}])["level"] == "check"
    assert verdict([{"title": "A", "size_match": None, "same": False}])["level"] == "ok"
    assert verdict([], warnings=["码率偏低"]) == {"level": "check", "reasons": ["Film：码率偏低"]}
    errored = build_kits(sites[:1], NAMES, TITLE, {}, discs, [{"site": "blutopia", "error": "超时", "items": []}])[0]
    assert errored["verdict"]["level"] == "check"
    untitled = build_kits(sites[:1], None, None, {}, discs, None)[0]
    assert untitled["fields"][0]["value"] == "" and untitled["verdict"]["level"] == "check"
    assert any("IMDb" in r for r in untitled["verdict"]["reasons"])
