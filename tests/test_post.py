import pytest

from whatdvd.post import PostDisc, TemplateError, render_post


def test_default_template() -> None:
    post = render_post([PostDisc("Disc 1", "General\nFormat : MPEG-PS\n\n", ["https://img/1.png", "https://img/2.png"])])
    assert post == (
        "[b]Disc 1[/b]\n"
        "[quote]\n"
        "General\nFormat : MPEG-PS\n"
        "[/quote]\n"
        "[img]https://img/1.png[/img]\n"
        "[img]https://img/2.png[/img]\n"
    )


def test_multiple_discs_separated_by_blank_line() -> None:
    post = render_post([PostDisc("D1", "m1", ["u1"]), PostDisc("D2", "m2", ["u2"])], "$name|$mediainfo|$screenshots")
    assert post == "D1|m1|[img]u1[/img]\n\nD2|m2|[img]u2[/img]\n"


@pytest.mark.parametrize("template", ["$unknown", "$"])
def test_bad_template(template: str) -> None:
    with pytest.raises(TemplateError):
        render_post([PostDisc("D", "m", [])], template)
