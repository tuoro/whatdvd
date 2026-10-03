"""从 MediaInfo 看是否可能加过俄语配音。"""

import pytest

from whatdvd.compliance import added_dub_warning, audio_tracks


def _report(vob_audio: list[str], ifo_audio: list[str | None]) -> str:
    """同 whatdvd 的 MediaInfo 报告：VOB 在前（音轨通常没有语言），三个换行，IFO 在后。"""
    vob = "General\nComplete name : Film/VIDEO_TS/VTS_01_1.VOB\n\nVideo\nFormat : MPEG Video\n\n"
    vob += "".join(f"Audio #{i}\nFormat : {fmt}\nChannel(s) : 2 channels\n\n" for i, fmt in enumerate(vob_audio, 1))
    ifo = "General\nComplete name : Film/VIDEO_TS/VTS_01_0.IFO\n\nVideo\nFormat : MPEG Video\n\n"
    for i, language in enumerate(ifo_audio, 1):
        ifo += f"Audio #{i}\nFormat : AC-3\nChannel(s) : 2 channels\n" + (f"Language : {language}\n" if language else "") + "\n"
    return vob + "\n\n\n" + ifo


def test_audio_tracks_come_from_ifo() -> None:
    tracks = audio_tracks(_report(["AC-3", "AC-3"], ["Russian", "English"]))
    assert [t.language for t in tracks] == ["Russian", "English"]
    assert audio_tracks("Audio\nFormat : AC-3\nLanguage : German\n") == audio_tracks("Audio\nFormat : AC-3\nLanguage : German")


@pytest.mark.parametrize(
    ("languages", "warns"),
    [
        (["Russian", "Russian", "Russian", "Russian", "English"], True),  # rutor 上的 Netherworld：4 条俄语 + 原声
        (["Russian", "Russian", "Italian"], True),
        (["Russian", "English"], False),  # 俄罗斯正版盘常见：一条俄语配音 + 原声
        (["Russian", "Russian"], False),  # 俄语片：5.1 + 2.0 两条俄语，没有外语
        (["English", "French", "German"], False),
        (["Russian", "Russian", None], False),  # 没有语言标记的不算原声
        ([], False),
    ],
)
def test_added_dub_warning(languages: list[str | None], warns: bool) -> None:
    warning = added_dub_warning(_report(["AC-3"] * len(languages), languages))
    assert (warning is not None) is warns
    if warning:
        assert "Custom" in warning and f"{languages.count('Russian')} 条俄语音轨" in warning
