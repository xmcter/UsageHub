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
            if parsed.path == "/api/usage":
                force = parse_qs(parsed.query).get("force", ["0"])[0] == "1"
                try:
                    payload = _get_usage(cfg, force)
                    self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                               "application/json; charset=utf-8")
                except Exception as e:
                    self._send(500, json.dumps({"error": str(e)}).encode("utf-8"),
                               "application/json; charset=utf-8")
            elif parsed.path in ("/", "/index.html"):
                self._send(200, INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
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
    httpd = ThreadingHTTPServer((host, port), make_handler(cfg))
    shown = host if host != "0.0.0.0" else "0.0.0.0（局域网设备用本机 IP 访问）"
    print("UsageHub 面板: http://{}:{}/".format(shown, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
