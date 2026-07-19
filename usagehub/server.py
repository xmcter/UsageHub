# -*- coding: utf-8 -*-
"""本地 Web 面板：stdlib http.server，无额外依赖。

GET /            → 单页面板
GET /api/usage   → JSON（带缓存，?force=1 强制刷新）
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .core import run_probes
from .providers import build_probes

INDEX_HTML = Path(__file__).parent / "web" / "index.html"

_cache = {"ts": 0.0, "data": None}
_lock = threading.Lock()


def _get_usage(cfg, force=False):
    ttl = int(cfg.get("cache_seconds", 300))
    with _lock:
        if not force and _cache["data"] is not None and time.time() - _cache["ts"] < ttl:
            return _cache["data"]
    results = [r.to_dict() for r in run_probes(build_probes(cfg))]
    payload = {"results": results, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "cache_seconds": ttl}
    with _lock:
        _cache["ts"] = time.time()
        _cache["data"] = payload
    return payload


def make_handler(cfg):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            
            # 静态页面免密访问以允许前端 JS 载入
            if parsed.path in ("/", "/index.html"):
                self._send(200, INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
                return

            # API 请求执行 Basic Auth 校验
            expected_username = cfg.get("auth_username", "admin")
            expected_password = cfg.get("auth_password", "")
            if expected_password:
                import base64
                auth_header = self.headers.get("Authorization")
                authenticated = False
                if auth_header and auth_header.startswith("Basic "):
                    try:
                        encoded = auth_header.split(" ", 1)[1]
                        decoded = base64.b64decode(encoded).decode("utf-8")
                        user, pwd = decoded.split(":", 1)
                        if user == expected_username and pwd == expected_password:
                            authenticated = True
                    except Exception:
                        pass
                if not authenticated:
                    self.send_response(401)
                    # 不返回 WWW-Authenticate 标头以阻止浏览器弹出原生 Basic Auth 登录框，由前端进行美化并保存密码至 localStorage
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"Unauthorized")
                    return

            if parsed.path == "/api/usage":
                force = parse_qs(parsed.query).get("force", ["0"])[0] == "1"
                try:
                    payload = _get_usage(cfg, force)
                    self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                               "application/json; charset=utf-8")
                except Exception as e:
                    self._send(500, json.dumps({"error": str(e)}).encode("utf-8"),
                               "application/json; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass  # 静默访问日志

    return Handler


def serve(cfg, host, port):
    if not cfg.get("auth_password"):
        import secrets
        new_pass = secrets.token_hex(4)
        cfg["auth_password"] = new_pass
        from .config import save_config
        try:
            save_config(cfg)
            print("【安全验证】已为您自动生成随机密码 '{}' 并写入 config.json".format(new_pass))
        except Exception as e:
            print("【安全警告】自动保存随机密码失败: {}".format(e))

    httpd = ThreadingHTTPServer((host, port), make_handler(cfg))
    shown = host if host != "0.0.0.0" else "0.0.0.0（局域网设备用本机 IP 访问）"
    print("UsageHub 面板: http://{}:{}/".format(shown, port))
    if cfg.get("auth_password"):
        print("访问用户名: {}".format(cfg.get("auth_username", "admin")))
        print("访问密码: {}".format(cfg["auth_password"]))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
