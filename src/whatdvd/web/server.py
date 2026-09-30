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


def _url(host: str, port: int) -> str:
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}/"


def serve(config: ServerConfig) -> None:
    app = create_app(config)
    url = _url(config.host, config.port)
    print(f"whatdvd Web 界面：{url}")
    print(f"允许浏览的目录：{'、'.join(str(root) for root in config.roots)}")
    print(f"输出目录：{config.output_dir}")
    if config.token_generated:
        # 只有随机生成的 token 才打印；配置文件里的 token 不写进日志
        print(f"本次启动随机生成了 token，重启后失效；在配置文件中设置 token 可固定。登录链接：{url}?token={config.token}")
    if not _is_loopback(config.host):
        print("警告：监听在非本机地址。请通过 HTTPS 反向代理或 SSH 隧道访问，不要直接暴露到公网。")
    uvicorn.run(app, host=config.host, port=config.port, proxy_headers=True, log_level="info")
