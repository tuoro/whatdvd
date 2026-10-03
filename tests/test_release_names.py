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
        ("Непобедимые (Ленинградцы) 1942 РУ DVD-5", "Непобедимые", 1942),  # 括号里是别名
        ("Film (Director's Cut) (1999) DVD9", "Film", 1999),
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


@pytest.mark.parametrize(
    ("folder", "titles", "reflects"),
    [
        # PTP《Site Policies About Modifying Files》的例子：不必完全一致，但要看得出片名
        ("MONTY_PYTHON_HOLY_GRAIL", ["Monty Python and the Holy Grail"], True),
        ("MPHGRAIL", ["Monty Python and the Holy Grail"], False),
        # rutor 上实际的文件夹名
        ("Imaginary_Heroes", ["Imaginary Heroes"], True),
        ("Saving.Private.Ryan.1998.DVD9.(custom)", ["Saving Private Ryan"], True),
        ("Juriev.den.2008.O.DVD_RUSSFILM", ["St. George's Day", "Yurev den"], True),  # 转写方式不同
        ("Иди и смотри (1985) DVD9", ["Come and See", "Idi i smotri"], True),  # 俄文原名
        ("NAPOLEON", ["Napoléon"], True),
        ("2k2", ["Dva kapitana II"], False),
        ("VIDEO_TS", ["Resident Evil: Degeneration"], False),
        ("Жизнь как чудо", ["Life Is a Miracle", "Zivot je cudo"], False),  # 俄文译名，不是原名
        ("Терминатор 2", ["Terminator 2: Judgment Day"], False),
        ("Predator 2", ["Predator"], False),  # 续集编号对不上
        ("Alien", ["Aliens"], False),
        ("Rocky.1976.DVD9", ["Rocky"], True),
    ],
)
def test_folder_reflects_title(folder: str, titles: list[str], reflects: bool) -> None:
    from whatdvd.release_names import folder_reflects_title

    assert folder_reflects_title(folder, titles) is reflects


def test_guess_queries_all_names() -> None:
    from whatdvd.release_names import guess_queries

    assert guess_queries("Одиночное плавание / Im Alleingang / Solo Voyage (1985) DVD9") == (
        ["Im Alleingang", "Solo Voyage", "Одиночное плавание"], 1985)
    assert guess_queries("The.Emerald.Forest.1985.PAL.DVD9") == (["The Emerald Forest"], 1985)


@pytest.mark.parametrize(
    ("text", "queries"),
    [
        # kinozal：俄文名和原名之间没有斜杠
        ("Дюплекс Duplex 2003 DUB, Sub DVD-9", ["Duplex", "Дюплекс"]),
        ("BBC: Жизнь в микромире Life in the undergrowth - E5 - 2005 VO, Sub 2 x DVD-9",
         ["Life in the undergrowth", "BBC: Жизнь в микромире"]),
        ("А был ли Каротин - E2 - 1989 РУ DVD-9", ["А был ли Каротин"]),
        # rutracker：斜杠只有一边有空格；季、集、碟号不是片名
        ("ДИСКИ 12 и 13 из 13 (V.Ray release) Твин Пикс [DVD9] S2E20-22 + диск допами/ Twin Peaks [1990, США, DVD9]",
         ["Twin Peaks", "Твин Пикс"]),
        ("Смешарики. Выпуск 7. Футбол / Смешарики / S1E8 of 216 (Денис Чернов) [2006, Россия, DVD5]",
         ["Смешарики. Футбол", "Смешарики", "Смешарики. Футбол Смешарики"]),
        ("Вершина Денали 3Д / Up Denali 3D [2006, документальный, DVD5]", ["Up Denali 3D", "Вершина Денали 3Д"]),
        # 已经有斜杠分开的原名时，俄文名里的拉丁字母不拆出来
        ("Гангста Love / Rob the Mob (2014) DVD5", ["Rob the Mob", "Гангста Love"]),
        ("Голодные игры: Сойка-пересмешница. Часть II / The Hunger Games: Mockingjay - Part 2 (2015) DVD9",
         ["The Hunger Games: Mockingjay - Part 2", "Голодные игры: Сойка-пересмешница. Часть II"]),
        ("Частная жизнь Генриха VIII The Private Life Of Henry VIII 1933 DVO DVD-5",
         ["The Private Life Of Henry VIII", "Частная жизнь Генриха VIII"]),
        ("Я возьму эту женщину I Take This Woman 1940 AVO (Яковлев) DVD-5", ["I Take This Woman", "Я возьму эту женщину"]),
        ("В 3:10 на Юму 3:10 to Yuma 1957 MVO DVD-5", ["3:10 to Yuma", "to Yuma", "В 3:10 на Юму 3:10"]),
        ("Терминатор 2 Terminator 2: Judgment Day 1991 DVD-9",
         ["2 Terminator 2: Judgment Day", "Terminator 2: Judgment Day", "Терминатор 2"]),
        # kinozal：年份范围取第一个，集数范围整个去掉
        ("Русь изначальная  - E2 - 1985-1986  РУ, Sub DVD-9", ["Русь изначальная"]),
        ("В поисках капитана Гранта  - E1-7 - 1985  РУ 3 x DVD-9", ["В поисках капитана Гранта"]),
        ("Черепашки мутанты ниндзя  Teenage Mutant Ninja Turtles - S1-9E1-183 - 1987-1995  MVO DVD-9 + 7 x DVD-5",
         ["Teenage Mutant Ninja Turtles", "Черепашки мутанты ниндзя"]),
        ("Энтузиазм / Симфония Донбасса [1930, документальное, DVD9]",
         ["Энтузиазм", "Симфония Донбасса", "Энтузиазм Симфония Донбасса"]),
    ],
)
def test_guess_queries_tracker_formats(text: str, queries: list[str]) -> None:
    from whatdvd.release_names import guess_queries

    assert guess_queries(text)[0] == queries
