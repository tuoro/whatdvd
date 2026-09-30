from pathlib import Path

import httpx
import pytest

from whatdvd.upload import HostUnreachable, Pixhost, UploadedImage, UploadError, pixhost_direct_url, upload_all


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

    with pytest.raises(HostUnreachable, match="连不上 Pixhost：refused"):
        Pixhost(transport=httpx.MockTransport(handler)).upload(_png(tmp_path))


def test_upload_read_timeout_fails_only_this_image(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(UploadError, match="上传到 Pixhost 失败") as info:
        Pixhost(transport=httpx.MockTransport(handler)).upload(_png(tmp_path))
    assert not isinstance(info.value, HostUnreachable)


class FlakyHost:
    name = "Flaky"

    def upload(self, path: Path) -> UploadedImage:
        if "02" in path.name:
            raise UploadError("boom")
        return UploadedImage(f"https://img/{path.name}", "t", "p")


def test_upload_all_continues_after_failure() -> None:
    messages: list[str] = []
    paths = [Path("a.scr01.png"), Path("a.scr02.png"), Path("a.scr03.png")]
    results = upload_all(FlakyHost(), paths, lambda message, finished: messages.append(message))
    assert [r.image is not None for r in results] == [True, False, True]
    assert results[1].error == "boom"
    assert "a.scr02.png 上传中…" in messages
    assert "a.scr02.png 上传失败：boom" in messages


class DeadHost:
    name = "Dead"

    def __init__(self) -> None:
        self.calls = 0

    def upload(self, path: Path) -> UploadedImage:
        self.calls += 1
        raise HostUnreachable("连不上 Pixhost：timed out")


def test_upload_all_stops_when_host_unreachable() -> None:
    host = DeadHost()
    events: list[tuple[str, bool]] = []
    paths = [Path(f"a.scr0{i}.png") for i in (1, 2, 3)]
    results = upload_all(host, paths, lambda message, finished: events.append((message, finished)))
    assert host.calls == 1  # 第一张连不上后不再尝试
    assert [r.unreachable for r in results] == [True, True, True]
    assert all(r.image is None for r in results)
    assert sum(finished for _, finished in events) == 3  # 每张都算处理完，进度能走到头
    assert ("a.scr03.png 未上传：连不上图床", True) in events


def test_pixhost_connect_timeout_is_unreachable(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(HostUnreachable, match="连不上 Pixhost"):
        Pixhost(transport=httpx.MockTransport(handler)).upload(_png(tmp_path))
