"""PTP 发种名称、BHD 标题、从文件夹名猜搜索词。"""

import pytest

from whatdvd.release_names import audio_from_mediainfo, bhd_title, disc_kind, guess_query, needs_aka, ptp_name


@pytest.mark.parametrize(
    ("text", "query", "year"),
    [
        ("Изумрудный лес / The Emerald Forest (1985) DVD9 | P", "The Emerald Forest", 1985),
        ("Изумрудный лес / The Emerald Forest [1985, Великобритания, боевик, DVD9 (Custom)] MVO", "The Emerald Forest", 1985),
        ("The.Emerald.Forest.1985.PAL.DVD9", "The Emerald Forest", 1985),
        ("Изумрудный лес (1985) DVD5", "Изумрудный лес", 1985),
        ("2001 A Space Odyssey (1968) DVD9", "2001 A Space Odyssey", 1968),
        ("Mallrats DVD9", "Mallrats", None),
        ("Юрьев день (2008) DVD9", "Юрьев день", 2008),
        ("Film_Name_2004_NTSC_2xDVD9", "Film Name", 2004),
    ],
)
def test_guess_query(text: str, query: str, year: int | None) -> None:
    assert guess_query(text) == (query, year)


@pytest.mark.parametrize(
    ("types", "kind"),
    [(["DVD9"], "DVD9"), (["DVD5"], "DVD5"), (["DVD9", "DVD9"], "2xDVD9"), (["DVD5", "DVD9"], "DVD9+DVD5"), ([], "")],
)
def test_disc_kind(types: list[str], kind: str) -> None:
    assert disc_kind(types) == kind


@pytest.mark.parametrize(
    ("title", "year", "kind", "name"),
    [
        ("The Emerald Forest", 1985, "DVD5", "The.Emerald.Forest.1985.DVD5"),
        ("The Lord of the Rings: The Two Towers", 2002, "DVD9", "The.Lord.of.the.Rings.The.Two.Towers.2002.DVD9"),
        ("Schindler's List", 1993, "2xDVD9", "Schindlers.List.1993.2xDVD9"),
        ("Amélie", 2001, "DVD9", "Amelie.2001.DVD9"),
        ("Fast & Furious", 2009, "DVD9", "Fast.and.Furious.2009.DVD9"),
        ("Mr. Smith Goes to Washington", 1939, "DVD5", "Mr.Smith.Goes.to.Washington.1939.DVD5"),
        ("Spider-Man 2", 2004, "DVD9", "Spider-Man.2.2004.DVD9"),
        ("Film", None, "DVD9", "Film.DVD9"),
    ],
)
def test_ptp_name(title: str, year: int | None, kind: str, name: str) -> None:
    assert ptp_name(title, year, kind) == name


@pytest.mark.parametrize(
    ("title", "original", "language", "aka"),
    [
        ("The Emerald Forest", "The Emerald Forest", "en", False),
        ("Come and See", "Иди и смотри", "ru", True),
        ("Amélie", "Le Fabuleux Destin d'Amélie Poulain", "fr", True),
        ("Ran", "乱", "ja", True),
        ("Solaris", "Солярис", "ru", True),
        ("Leon", "Léon", "fr", False),  # 只差一个变音符号
        ("Andrei Rublev", "", "ru", False),
    ],
)
def test_needs_aka(title: str, original: str, language: str, aka: bool) -> None:
    assert needs_aka(title, original, language) is aka


def _mediainfo(*audio: tuple[str, str, str]) -> str:
    text = "General\nComplete name : Film/VIDEO_TS/VTS_01_1.VOB\n\nVideo\nFormat : MPEG Video\nWidth : 720 pixels\n\n"
    for index, (fmt, channels, layout) in enumerate(audio, start=1):
        text += f"Audio #{index}\nID : 189 (0xBD)-{127 + index}\nFormat : {fmt}\nChannel(s) : {channels}\n"
        text += f"Channel layout : {layout}\n\n" if layout else "\n"
    return text


@pytest.mark.parametrize(
    ("audio", "expected"),
    [
        ([("AC-3", "6 channels", "L R C LFE Ls Rs")], "DD5.1"),
        ([("AC-3", "2 channels", "L R")], "DD2.0"),
        ([("AC-3", "1 channel", "C")], "DD1.0"),
        ([("DTS", "6 channels", "C L R Ls Rs LFE")], "DTS 5.1"),
        ([("MPEG Audio", "2 channels", "")], "MP2 2.0"),
        ([("PCM", "2 channels", "L R")], "LPCM 2.0"),
        ([("AC-3", "6 channels", "")], "DD5.1"),  # 没有声道布局时 6 声道按 5.1
        ([("AC-3", "2 channels", "L R"), ("AC-3", "6 channels", "L R C LFE Ls Rs")], "DD2.0"),  # 第一条
    ],
)
def test_audio_from_mediainfo(audio: list[tuple[str, str, str]], expected: str) -> None:
    assert audio_from_mediainfo(_mediainfo(*audio)) == expected


def test_audio_missing() -> None:
    assert audio_from_mediainfo("General\nFormat : MPEG-PS\n\nVideo\nFormat : MPEG Video\n") is None


@pytest.mark.parametrize(
    ("kwargs", "title"),
    [
        (
            {"title": "The Emerald Forest", "original_title": "The Emerald Forest", "original_language": "en",
             "year": 1985, "standard": "PAL", "kind": "DVD9", "audio": "DD5.1"},
            "The Emerald Forest 1985 PAL DVD9 MPEG-2 DD5.1",
        ),
        (
            {"title": "Come and See", "original_title": "Иди и смотри", "original_language": "ru", "year": 1985,
             "standard": "PAL", "kind": "DVD9", "audio": "DD5.1", "region": "RUS"},
            "Come and See AKA Иди и смотри 1985 RUS PAL DVD9 MPEG-2 DD5.1",
        ),
        (
            {"title": "Blade Runner", "year": 1982, "standard": "NTSC", "kind": "2xDVD9", "audio": "DTS 5.1",
             "edition": "Final Cut", "region": "Warner"},
            "Blade Runner 1982 Final Cut Warner NTSC 2xDVD9 MPEG-2 DTS 5.1",
        ),
        ({"title": "Film", "year": None, "standard": None, "kind": "DVD5", "audio": None}, "Film DVD5 MPEG-2"),
    ],
)
def test_bhd_title(kwargs: dict[str, object], title: str) -> None:
    assert bhd_title(**kwargs) == title  # type: ignore[arg-type]
