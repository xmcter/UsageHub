# -*- coding: utf-8 -*-
"""生成端到端加密的静态快照页，推到常开的静态站（Vercel）。

数据全在本机（浏览器 cookie / 本地 RPC / cc-switch / 钥匙串），电脑关机就没有实时数据。
本模块让 Mac 开机时定期把最新用量加密后推到云上：
  - 用访问密码派生 AES-GCM 密钥（PBKDF2-SHA256），只把密文塞进静态页；
  - 公网页面永远只有密文，手机端输密码在浏览器本地解密渲染；
  - Mac 关机后页面照样能打开，显示最后一次快照 + "本机离线"。

加密格式（与 web/index.html 的 Web Crypto 对齐）：
  base64( salt[16] || iv[12] || AES-256-GCM(ciphertext||tag) )
  PBKDF2-HMAC-SHA256, iterations=200000, key=32B, iv=12B
"""
import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

from .config import load_config
from .core import run_probes, utcnow_iso
from .providers import build_probes

PBKDF2_ITERS = 200000
WEB_INDEX = Path(__file__).parent / "web" / "index.html"


def _encrypt(plaintext: bytes, password: str) -> str:
    salt = os.urandom(16)
    iv = os.urandom(12)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITERS)
    key = kdf.derive(password.encode("utf-8"))
    ct = AESGCM(key).encrypt(iv, plaintext, None)  # 返回 ciphertext||tag，与 WebCrypto 一致
    return base64.b64encode(salt + iv + ct).decode("ascii")


def build_snapshot_html(cfg: dict, results=None) -> str:
    """跑一轮探测 → 加密 payload → 注入 web/index.html → 返回可部署的 HTML 字符串。"""
    password = (cfg.get("auth_password") or "").strip()
    if not password:
        raise RuntimeError("未设置 auth_password，无法加密快照；请先在 ~/.usagehub/config.json 配置访问密码")

    if results is None:
        results = [r.to_dict() for r in run_probes(build_probes(cfg, None))]
    payload = {"results": results, "generated_at": utcnow_iso()}
    blob = _encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"), password)

    html = WEB_INDEX.read_text(encoding="utf-8")
    inject = (
        '<script>window.__SNAPSHOT__ = {{"blob":{blob},"generatedAt":{gen}}};</script>'
    ).format(blob=json.dumps(blob), gen=json.dumps(payload["generated_at"]))
    # 注入到主 <script> 之前，确保 SNAP 常量能读到
    marker = "<script>\n// 快照模式"
    if marker not in html:
        raise RuntimeError("web/index.html 结构变了，找不到注入点（主 script 标记）")
    return html.replace(marker, inject + "\n<script>\n// 快照模式", 1)


def write_snapshot(cfg: dict, out_dir: Path, results=None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    html = build_snapshot_html(cfg, results=results)
    out_file = out_dir / "index.html"
    out_file.write_text(html, encoding="utf-8")
    return out_file
