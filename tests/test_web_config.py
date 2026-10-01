from pathlib import Path

import pytest

from whatdvd.post import DEFAULT_TEMPLATE
from whatdvd.web.config import TOKEN_ENV, ConfigError, load_config


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_full_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "media"
    root.mkdir()
    template = tmp_path / "post.txt"
    template.write_text("$name\n$screenshots", encoding="utf-8")
    config = load_config(
        write(
            tmp_path,
            f"""
host = "0.0.0.0"
port = 9000
token = "secret"
roots = ["{root}"]
output_dir = "{tmp_path}/out"
max_jobs = 2

[screenshots]
count = 6

[pixhost]
domain = "pixhost.cc"
proxy = "http://127.0.0.1:7890"

[torrent]
announces = [" https://t.example/a "]
piece_length = 22

[post]
template = "{template}"
""",
        )
    )
    assert config.roots == (root.resolve(),)
    assert (config.host, config.port, config.token, config.token_source) == ("0.0.0.0", 9000, "secret", "config")
    assert config.output_dir == tmp_path / "out"
    assert (config.max_jobs, config.screenshot_count) == (2, 6)
    assert (config.pixhost_domain, config.proxy) == ("pixhost.cc", "http://127.0.0.1:7890")
    assert (config.announces, config.piece_length) == (("https://t.example/a",), 22)
    assert config.template == "$name\n$screenshots"


def test_defaults_and_overrides(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, ""), host="::1", port=1234, roots=[tmp_path])
    assert config.roots == (tmp_path.resolve(),)
    assert (config.host, config.port) == ("::1", 1234)
    assert config.token_source == "new" and len(config.token) >= 24
    assert config.template == DEFAULT_TEMPLATE
    assert config.piece_length == 24 and config.max_jobs == 1


def test_token_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, "from-env")
    config = load_config(write(tmp_path, ""), roots=[tmp_path])
    assert (config.token, config.token_source) == ("from-env", "env")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('hots = "x"', "未知的配置项：hots"),
        ("port = \"80\"", "port 的类型不对"),
        ("max_jobs = true", "max_jobs 的类型不对"),
        ("screenshots = 3", "应该是一个表"),
        ("[pixhost]\ndomain = \"imgbox.com\"", "pixhost.domain"),
        ("[torrent]\npiece_length = 30", "piece_length"),
        ("[torrent]\nannounces = [\"\"]", "announces"),
        ("[post]\ntemplate = \"/nonexistent/template.txt\"", "模板"),
        ("port = 0", "端口无效"),
        ("[screenshots]\naspect = \"square\"", "screenshots.aspect"),
        ("roots = [", "格式有误"),
    ],
)
def test_invalid_config(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, text), roots=[tmp_path])


def test_roots_required_and_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="至少需要一个"):
        load_config(write(tmp_path, ""))
    with pytest.raises(ConfigError, match="不存在"):
        load_config(write(tmp_path, ""), roots=[tmp_path / "missing"])


def test_missing_explicit_config_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="无法读取"):
        load_config(tmp_path / "nope.toml", roots=[tmp_path])


def test_example_config_is_valid(tmp_path: Path) -> None:
    example = Path(__file__).parent.parent / "config.example.toml"
    config = load_config(example, roots=[tmp_path])
    assert config.host == "127.0.0.1" and config.port == 26873
    assert config.piece_length == 24 and config.proxy is None


def test_generated_token_is_saved_and_reused(tmp_path: Path, isolated_home: Path) -> None:
    first = load_config(write(tmp_path, ""), roots=[tmp_path])
    token_file = isolated_home / ".local/share/whatdvd/token"
    assert first.token_source == "new" and first.token_file == token_file
    assert token_file.read_text().strip() == first.token
    assert token_file.stat().st_mode & 0o777 == 0o600
    second = load_config(write(tmp_path, ""), roots=[tmp_path])
    assert (second.token, second.token_source) == (first.token, "file")


def test_custom_token_file(tmp_path: Path) -> None:
    token_file = tmp_path / "state" / "token"
    config = load_config(write(tmp_path, f'token_file = "{token_file}"'), roots=[tmp_path])
    assert config.token_file == token_file and token_file.is_file()


def test_config_path_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATDVD_CONFIG", str(write(tmp_path, "port = 30001")))
    assert load_config(roots=[tmp_path]).port == 30001


def test_token_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from whatdvd.cli import main

    config = write(tmp_path, f'port = 30002\nhost = "0.0.0.0"\ntoken_file = "{tmp_path}/token"')
    assert main(["token", "-c", str(config)]) == 0
    first = capsys.readouterr().out
    token = (tmp_path / "token").read_text().strip()
    assert f"token：{token}（刚刚生成" in first
    assert f"登录链接：http://127.0.0.1:30002/?token={token}" in first
    assert main(["token", "-c", str(config)]) == 0
    assert f"token：{token}（保存在 {tmp_path}/token）" in capsys.readouterr().out


def test_qbittorrent_and_jackett(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setenv("WHATDVD_QB_PASSWORD", "from-env")
    monkeypatch.setenv("WHATDVD_JACKETT_API_KEY", "key-from-env")
    config = load_config(
        write(
            tmp_path,
            f"""
roots = ["{root}"]
token = "t"
database = "{tmp_path}/state.db"

[qbittorrent]
url = "http://qb:8080/"
username = "admin"
path_map = {{ "/downloads/" = "/media/qb/" }}
interval = 30

[jackett]
url = "http://jackett:9117"
indexer = "rutracker"
queries = ["DVD9"]
interval = 0
""",
        )
    )
    assert config.database == tmp_path / "state.db"
    assert config.qbit is not None and config.jackett is not None
    assert (config.qbit.url, config.qbit.password, config.qbit.category) == ("http://qb:8080", "from-env", "whatdvd")
    assert config.qbit.path_map == (("/downloads", "/media/qb"),)
    assert (config.jackett.api_key, config.jackett.indexer, config.jackett.queries) == ("key-from-env", "rutracker", ("DVD9",))
    assert config.jackett.interval == 0


def test_without_qbittorrent_and_jackett(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    config = load_config(write(tmp_path, f'roots = ["{root}"]\ntoken = "t"\n'))
    assert config.qbit is None and config.jackett is None


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ('[qbittorrent]\nurl = "qb:8080"', "必须以 http://"),
        ('[qbittorrent]\nurl = "http://qb"\npath_map = { "downloads" = "/media" }', "绝对路径"),
        ('[qbittorrent]\nurl = "http://qb"\ninterval = 5', "不能小于 10 秒"),
        ('[qbittorrent]\nurl = "http://qb"\ncategory = " "', "category 不能为空"),
        ('[jackett]\nurl = "http://jackett"', "api_key"),
        ('[jackett]\nurl = "http://j"\napi_key = "k"\nqueries = []', "非空字符串列表"),
        ('[jackett]\nurl = "http://j"\napi_key = "k"\ninterval = 5', "不能小于 10 分钟"),
        ('[jackett]\nurl = "http://j"\napi_key = "k"\nunknown = 1', "未知的配置项"),
    ],
)
def test_invalid_qbittorrent_and_jackett(tmp_path: Path, section: str, message: str) -> None:
    root = tmp_path / "media"
    root.mkdir()
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, f'roots = ["{root}"]\ntoken = "t"\n{section}\n'))
