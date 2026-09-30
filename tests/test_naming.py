import pytest

from whatdvd.naming import clean_title, mediainfo_name, screenshot_name


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Movie Name (2001)", "Movie.Name.2001"),
        ("Tab\tSeparated", "Tab.Separated"),
        ("Trailing ", "Trailing."),
        ("No.Change", "No.Change"),
        ("全角　空格", "全角　空格"),  # GNU tr 不认非 ASCII 空白
        ("VTS_01_1.VOB", "VTS_01_1.VOB"),
    ],
)
def test_clean_title(title: str, expected: str) -> None:
    assert clean_title(title) == expected


@pytest.mark.parametrize(
    ("index", "count", "expected"),
    [
        (1, 10, "T.scr01.png"),
        (10, 10, "T.scr10.png"),
        (3, 9, "T.scr3.png"),
        (7, 100, "T.scr007.png"),
    ],
)
def test_screenshot_name_pads_like_seq_w(index: int, count: int, expected: str) -> None:
    assert screenshot_name("T", index, count) == expected


def test_mediainfo_name() -> None:
    assert mediainfo_name("Disc.VTS_01_1.VOB") == "Disc.VTS_01_1.VOB.mediainfo.txt"
