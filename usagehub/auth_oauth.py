# -*- coding: utf-8 -*-
"""浏览器 OAuth 登录（不依赖 CodexBar）。

用法：usagehub auth antigravity
流程：本机起临时回调 → 打开 Google 登录页 → 换 refresh_token → 写入 config.accounts
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

from .config import load_config, save_config
from .providers.antigravity import DEFAULT_CLIENTS

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
SCOPES = " ".join([
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/cloud-platform",
])


def auth_antigravity(timeout: int = 300) -> int:
    client_id, client_secret = DEFAULT_CLIENTS[0]
    if not client_id:
        print("未配置 OAuth client_id")
        return 1

    state = secrets.token_urlsafe(24)
    result = {"code": "", "error": "", "done": threading.Event()}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path not in ("/", "/callback", "/oauth2callback"):
                self.send_response(404)
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(parsed.query)
            if qs.get("state", [""])[0] != state:
                self._html(400, "state 不匹配，请关闭后重试。")
                result["error"] = "state mismatch"
                result["done"].set()
                return
            if qs.get("error"):
                err = qs.get("error", [""])[0]
                self._html(400, "授权失败：{}".format(err))
                result["error"] = err
                result["done"].set()
                return
            code = qs.get("code", [""])[0]
            if not code:
                self._html(400, "未收到 authorization code")
                result["error"] = "no code"
                result["done"].set()
                return
            result["code"] = code
            self._html(200, "登录成功，可以关闭此页回到终端。")
            result["done"].set()

        def log_message(self, fmt, *args):  # noqa: A003
            return

        def _html(self, status: int, msg: str):
            body = (
                "<!doctype html><meta charset=utf-8>"
                "<title>UsageHub</title>"
                "<body style='font-family:sans-serif;padding:40px'>"
                "<h2>UsageHub · Antigravity</h2><p>{}</p></body>"
            ).format(msg).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    # Desktop 客户端通常允许任意 localhost 端口
    redirect_uri = "http://127.0.0.1:{}/oauth2callback".format(port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent select_account",
        "include_granted_scopes": "true",
        "state": state,
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)
    print("将打开浏览器登录 Google（请选择目标账号）…")
    print("若未自动打开，请手动访问：\n{}\n".format(url))
    print("回调地址: {}".format(redirect_uri))
    opened = False
    try:
        import subprocess
        import sys
        if sys.platform == "darwin":
            subprocess.run(["open", url], check=False)
            opened = True
        elif sys.platform.startswith("win"):
            os.startfile(url)  # type: ignore[attr-defined]
            opened = True
    except Exception:
        opened = False
    if not opened:
        webbrowser.open(url)

    if not result["done"].wait(timeout):
        httpd.shutdown()
        print("超时（{}s）未完成授权".format(timeout))
        return 1
    httpd.shutdown()

    if result["error"] or not result["code"]:
        print("授权失败: {}".format(result["error"] or "unknown"))
        return 1

    tokens = _exchange_code(result["code"], redirect_uri, client_id, client_secret)
    if not tokens:
        # 兼容 redirect 路径差异再试一次无意义；已有 code
        print("用 code 换 token 失败（redirect_uri 可能未被 client 登记）")
        return 1

    refresh = tokens.get("refresh_token") or ""
    access = tokens.get("access_token") or ""
    if not refresh and not access:
        print("未拿到 token")
        return 1
    if not refresh:
        print("警告：未返回 refresh_token（可能该 Google 账号以前授过权）。")
        print("仍写入 access_token；过期后需重新 auth。")

    email = tokens.get("email") or _userinfo_email(access) or ""
    _save_account(
        email=email,
        refresh_token=refresh,
        access_token=access,
        client_id=client_id,
        client_secret=client_secret,
        expiry=tokens.get("expiry_date"),
    )
    print("✅ 已写入 ~/.usagehub/config.json")
    print("   账号: {}".format(email or "(未知，下次探测时解析)"))
    print("验证: .venv/bin/python -m usagehub --providers antigravity")
    return 0


def _exchange_code(code: str, redirect_uri: str, client_id: str, client_secret: str) -> Optional[dict]:
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode("utf-8")
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:400]
        print("token 接口 HTTP {}: {}".format(e.code, body))
        return None
    except Exception as e:
        print("token 请求失败: {}".format(type(e).__name__))
        return None

    out = {
        "access_token": payload.get("access_token") or "",
        "refresh_token": payload.get("refresh_token") or "",
        "id_token": payload.get("id_token") or "",
    }
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, (int, float)):
        out["expiry_date"] = (time.time() + float(expires_in)) * 1000.0
    # id_token 里可能有 email
    idt = out.get("id_token") or ""
    if idt.count(".") == 2:
        try:
            import base64
            pad = "=" * (-len(idt.split(".")[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(idt.split(".")[1] + pad))
            out["email"] = claims.get("email") or ""
        except Exception:
            pass
    return out


def _userinfo_email(access_token: str) -> str:
    if not access_token:
        return ""
    req = urllib.request.Request(
        USERINFO_URL,
        headers={"Authorization": "Bearer {}".format(access_token)},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8")).get("email") or ""
    except Exception:
        return ""


def _save_account(email, refresh_token, access_token, client_id, client_secret, expiry=None):
    cfg = load_config()
    prov = cfg.setdefault("providers", {}).setdefault("antigravity", {})
    accounts = list(prov.get("accounts") or [])
    email_l = (email or "").lower()
    entry = {
        "email": email or "",
        "refresh_token": refresh_token or "",
        "access_token": access_token or "",
        "client_id": client_id or "",
        "client_secret": client_secret or "",
    }
    if expiry is not None:
        entry["expiry_date"] = expiry

    replaced = False
    for i, a in enumerate(accounts):
        if not isinstance(a, dict):
            continue
        if email_l and (a.get("email") or "").lower() == email_l:
            accounts[i] = {**a, **entry}
            replaced = True
            break
    if not replaced:
        accounts.append(entry)
    prov["accounts"] = accounts
    prov["enabled"] = True
    save_config(cfg)


# ---------------------------------------------------------------------------
# MonoCloud 面板登录（邮箱密码 → session cookie）
# ---------------------------------------------------------------------------

MONOCLOUD_LOGIN = "https://mymonocloud.com/login"


def auth_monocloud(timeout: int = 60) -> int:
    """交互式登录 mymonocloud.com，把 session cookie 写入 config.providers.monocloud.accounts。

    不保存密码本身；只保存登录后的会话 cookie（与浏览器登录态等价）。
    支持登录两个套餐账号：重复运行本命令即可追加第二个账号。
    """
    import getpass

    email = input("MonoCloud 邮箱: ").strip()
    if not email:
        print("邮箱不能为空")
        return 1
    password = getpass.getpass("MonoCloud 密码（不会显示/保存）: ")

    import http.cookiejar
    import urllib.parse as urlparse

    # 1. 打开登录页拿 XSRF cookie
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(),
    )
    try:
        with opener.open(MONOCLOUD_LOGIN, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print("打开登录页失败: {}".format(e))
        return 1

    xsrf = _cookie_value(jar, "XSRF-TOKEN")
    if not xsrf:
        print("登录页未返回 XSRF cookie，无法继续（面板可能变更）")
        return 1

    # 2. POST 登录（Inertia：X-XSRF-TOKEN header + JSON body）
    import json as _json
    body = _json.dumps({"email": email, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        MONOCLOUD_LOGIN,
        data=body,
        method="POST",
        headers={
            "X-XSRF-TOKEN": urlparse.unquote(xsrf),
            "X-Inertia": "true",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "UsageHub/1.0",
            "Referer": MONOCLOUD_LOGIN,
        },
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            payload = _json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        if e.code in (302, 303, 409):
            print("登录失败（HTTP {}）: 邮箱或密码错误".format(e.code))
        else:
            print("登录失败 HTTP {}: {}".format(e.code, detail))
        return 1
    except Exception as e:
        print("登录请求失败: {}".format(type(e).__name__, e))
        return 1

    auth = payload.get("auth") if isinstance(payload, dict) else None
    user = (auth or {}).get("user") if isinstance(auth, dict) else None
    if not user:
        errs = (payload or {}).get("errors") if isinstance(payload, dict) else {}
        if isinstance(errs, dict) and errs:
            print("登录被拒: {}".format(_json.dumps(errs, ensure_ascii=False)))
        else:
            print("登录失败：未返回用户信息（面板可能要求验证码/设备验证）")
        return 1

    # 3. 组装 cookie 请求头并验证
    cookie_str = _jar_header(jar)
    session = cookie_str
    if not session:
        print("登录成功但未拿到 session cookie，无法保存")
        return 1

    account_email = (user.get("email") or user.get("name") or email).strip()
    print("✅ 登录成功: {}".format(account_email))

    # 4. 写入 config
    _save_monocloud_account(account_email, session)
    print("已保存到 ~/.usagehub/config.json（providers.monocloud.accounts）")
    print("验证: .venv/bin/python -m usagehub --providers monocloud")
    return 0


def _save_monocloud_account(email: str, session: str) -> None:
    cfg = load_config()
    prov = cfg.setdefault("providers", {}).setdefault("monocloud", {})
    accounts = list(prov.get("accounts") or [])
    email_l = (email or "").lower()
    entry = {"email": email or "", "session": session or ""}
    replaced = False
    for i, a in enumerate(accounts):
        if not isinstance(a, dict):
            continue
        if email_l and (a.get("email") or "").lower() == email_l:
            accounts[i] = {**a, **entry}
            replaced = True
            break
    if not replaced:
        accounts.append(entry)
    prov["accounts"] = accounts
    prov["enabled"] = True
    save_config(cfg)


def _cookie_value(jar: "http.cookiejar.CookieJar", name: str) -> str:
    for c in jar:
        if c.name == name:
            return c.value
    return ""


def _jar_header(jar: "http.cookiejar.CookieJar") -> str:
    return "; ".join("{}={}".format(c.name, c.value) for c in jar)
