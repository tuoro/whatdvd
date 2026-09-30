from pathlib import Path

import httpx
import pytest

from whatdvd.upload import Pixhost, UploadedImage, UploadError, pixhost_direct_url, upload_all


@pytest.mark.parametrize(
    ("thumb", "direct"),
    [
        ("https://t0.pixhost.to/thumbs/123/456_a.png", "https://img0.pixhost.to/images/123/456_a.png"),
        ("https://t12.pixhost.cc/thumbs/9/x.png", "https://img12.pixhost.cc/images/9/x.png"),
        ("https://T3.PIXHOST.TO/thumbs/1/y.png", "https://img3.pixhost.to/images/1/y.png"),
        ("https://other.example/thumbs/1/z.png", "https://other.example/images/1/z.png"),
    ],
)
def test_pixhost_direct_url(thumb: str, direct: str) -> None:
    assert pixhost_direct_url(thumb) == direct


def test_pixhost_direct_url_rejects_invalid() -> None:
    with pytest.raises(UploadError):
        pixhost_direct_url("ftp://t0.pixhost.to/thumbs/1.png")


def _png(tmp_path: Path) -> Path:
    path = tmp_path / "Disc.VTS_01_1.VOB.scr01.png"
    path.write_bytes(b"\x89PNG fake")
    return path


def test_upload_sends_pixhost_form_and_reads_urls(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"name": "x.png", "show_url": "https://pixhost.to/show/1/x.png", "th_url": "https://t1.pixhost.to/thumbs/1/x.png"},
        )

    host = Pixhost(transport=httpx.MockTransport(handler))
    image = host.upload(_png(tmp_path))
    assert image == UploadedImage(
        direct_url="https://img1.pixhost.to/images/1/x.png",
        thumbnail_url="https://t1.pixhost.to/thumbs/1/x.png",
        page_url="https://pixhost.to/show/1/x.png",
    )
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.pixhost.to/images"
    assert request.headers["Accept"] == "application/json"
    body = request.content
    assert b'name="img"; filename="Disc.VTS_01_1.VOB.scr01.png"' in body
    assert b'name="content_type"\r\n\r\n0' in body
    assert b'name="max_th_size"\r\n\r\n420' in body


def test_upload_uses_alternate_domain(tmp_path: Path) -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={"show_url": "https://pixhost.cc/s", "th_url": "https://t1.pixhost.cc/thumbs/1/x.png"})

    Pixhost("pixhost.cc", transport=httpx.MockTransport(handler)).upload(_png(tmp_path))
    assert urls == ["https://api.pixhost.cc/images"]


def test_unknown_domain_rejected() -> None:
    with pytest.raises(ValueError):
        Pixhost("example.com")


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(413, text="too large"), "HTTP 413"),
        (httpx.Response(200, text="<html>"), "无法识别"),
        (httpx.Response(200, json={"show_url": "x"}), "无法识别"),
    ],
)
def test_upload_errors(tmp_path: Path, response: httpx.Response, message: str) -> None:
    host = Pixhost(transport=httpx.MockTransport(lambda request: response))
    with pytest.raises(UploadError, match=message):
        host.upload(_png(tmp_path))


def test_upload_connection_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(UploadError, match="连接 Pixhost 失败"):
        Pixhost(transport=httpx.MockTransport(handler)).upload(_png(tmp_path))


class FlakyHost:
    name = "Flaky"

    def upload(self, path: Path) -> UploadedImage:
        if "02" in path.name:
            raise UploadError("boom")
        return UploadedImage(f"https://img/{path.name}", "t", "p")


def test_upload_all_continues_after_failure() -> None:
    messages: list[str] = []
    paths = [Path("a.scr01.png"), Path("a.scr02.png"), Path("a.scr03.png")]
    results = upload_all(FlakyHost(), paths, messages.append)
    assert [r.image is not None for r in results] == [True, False, True]
    assert results[1].error == "boom"
    assert "a.scr02.png 上传失败：boom" in messages
