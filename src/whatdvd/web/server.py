"""启动 Web 服务。"""

from __future__ import annotations

import ipaddress

import uvicorn

from .app import create_app
from .config import ServerConfig


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def login_url(host: str, port: int) -> str:
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}/"


def serve(config: ServerConfig) -> None:
    app = create_app(config)
    url = login_url(config.host, config.port)
    print(f"whatdvd Web 界面：{url}")
    print(f"允许浏览的目录：{'、'.join(str(root) for root in config.roots)}")
    print(f"输出目录：{config.output_dir}")
    # 只有第一次生成时才把 token 打印出来；之后只提示去哪里看，避免 token 反复出现在日志里
    if config.token_source == "new":
        print(f"已生成登录 token 并保存到 {config.token_file}，重启后不变。")
        print(f"登录链接：{url}?token={config.token}")
    elif config.token_source == "file":
        print(f"登录 token 保存在 {config.token_file}，运行 whatdvd token 查看。")
    elif config.token_source == "env":
        print("登录 token 来自环境变量 WHATDVD_TOKEN。")
    else:
        print("登录 token 来自配置文件。")
    if not _is_loopback(config.host):
        print("警告：监听在非本机地址。请通过 HTTPS 反向代理或 SSH 隧道访问，不要直接暴露到公网。")
    uvicorn.run(app, host=config.host, port=config.port, proxy_headers=True, log_level="info")
