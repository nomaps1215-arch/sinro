#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ローカルサーバーを立てて、ブラウザでアプリを開く。

    python tools/serve.py            # http://localhost:8765/ を開く
    python tools/serve.py --port 9000
    python tools/serve.py --no-open  # ブラウザは開かない

index.html は直接ファイルとして開いても動くが、学校の写真は外部サイトから
読み込むので、サーバー越しの方が実物に近い状態で確認できる。

止めるときは Ctrl+C。
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import socket
import socketserver
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8765
MAX_TRIES = 20


class Handler(http.server.SimpleHTTPRequestHandler):
    """アクセスログは1行ずつ出すと騒がしいので、エラーだけ残す。"""

    def log_message(self, fmt, *args):  # noqa: A002 - 親クラスの名前に合わせる
        pass

    def end_headers(self):
        # 編集したらすぐ反映されるように、キャッシュさせない
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def free_port(start: int) -> int | None:
    """使えるポートを探す。開発中に前のサーバーが残っていることがある。"""
    for port in range(start, start + MAX_TRIES):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-open", action="store_true", help="ブラウザを開かない")
    args = ap.parse_args()

    port = free_port(args.port)
    if port is None:
        print(f"ポート {args.port} から {args.port + MAX_TRIES - 1} まで全て使用中です。")
        return 1
    if port != args.port:
        print(f"ポート {args.port} は使用中だったので {port} を使います。")

    url = f"http://localhost:{port}/index.html"
    handler = functools.partial(Handler, directory=str(ROOT))
    socketserver.TCPServer.allow_reuse_address = True

    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        print(f"SINRO を配信しています： {url}")
        print(f"フォルダ： {ROOT}")
        print("止めるときは Ctrl+C")
        if not args.no_open:
            # サーバーが応答できるようになってから開く
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        with contextlib.suppress(KeyboardInterrupt):
            httpd.serve_forever()
    print("\n止めました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
