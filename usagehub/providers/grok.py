# -*- coding: utf-8 -*-
"""SuperGrok（grok.com 订阅）：网页内部接口 rest/rate-limits，需要浏览器 Cookie。

注意：这是未公开接口，字段随时可能变；cookie 过期需重新粘贴。
自动同步：若 config 中未配置 cookie，会尝试从 Chrome 本地 Cookies 数据库解密提取。
"""
import json
import os
import struct
import sqlite3
import subprocess
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path

try:
    from hashlib import pbkdf2_hmac
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

from .base import ProviderProbe
from ..core import Window

RATE_URL = "https://grok.com/rest/rate-limits"
USER_URL = "https://grok.com/rest/auth/get-user"
SUBS_URL = "https://grok.com/rest/subscriptions"
# 「每周 SuperGrok 限额」数据源（grok.com 设置→使用量 面板实际调用的 gRPC-Web 端点）
CREDITS_URL = "https://grok.com/grok_api_v2.GrokBuildBilling/GetGrokCreditsConfig"

# GetGrokCreditsConfig 里 product 枚举 → 展示名
_PRODUCT_NAMES = {1: "API", 4: "聊天", 5: "Imagine"}


def _grpc_web_message(data):
    """从 gRPC-Web 响应里取出第一个数据帧（flag 高位为 0 的帧）的 protobuf 负载。"""
    i = 0
    while i + 5 <= len(data):
        flag = data[i]
        ln = struct.unpack(">I", data[i + 1:i + 5])[0]
        i += 5
        payload = data[i:i + ln]
        i += ln
        if not (flag & 0x80):  # 数据帧（0x80 是 trailer）
            return payload
    return None


def _pb_parse(buf):
    """极简 protobuf 解析：返回 {field: [(wiretype, value), ...]}。
    value：varint→int，f32→float，bytes→bytes。"""
    out = {}
    i = 0
    n = len(buf)
    while i < n:
        tag, i = _read_varint(buf, i)
        field = tag >> 3
        wt = tag & 7
        if wt == 0:
            v, i = _read_varint(buf, i)
        elif wt == 5:
            v = struct.unpack("<f", buf[i:i + 4])[0]; i += 4
        elif wt == 1:
            v = struct.unpack("<d", buf[i:i + 8])[0]; i += 8
        elif wt == 2:
            ln, i = _read_varint(buf, i)
            v = buf[i:i + ln]; i += ln
        else:
            break
        out.setdefault(field, []).append((wt, v))
    return out


def _read_varint(buf, i):
    v = 0; s = 0
    while True:
        b = buf[i]; i += 1
        v |= (b & 0x7f) << s
        if not b & 0x80:
            return v, i
        s += 7


def _pb_first(parsed, field, kind):
    vals = parsed.get(field)
    return vals[0][1] if vals else None


def _pb_all(parsed, field, kind):
    return [v for (_wt, v) in parsed.get(field, [])]

# xAI 订阅等级枚举 → 对外营销名
# 注意：$30/月 档接口枚举名是 GROK_PRO（productId grok.pro.monthly.30），
# 但 xAI 对外就叫「SuperGrok」，按用户认知显示。
_TIER_NAMES = {
    "SUBSCRIPTION_TIER_GROK_PRO": "SuperGrok",
    "SUBSCRIPTION_TIER_GROK_SUPER": "SuperGrok",
    "SUBSCRIPTION_TIER_GROK_SUPER_HEAVY": "SuperGrok Heavy",
}

BROWSER_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://grok.com",
    "Referer": "https://grok.com/",
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "*/*",
}

# Chrome profile directories to search (ordered by preference)
_CHROME_BASE = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
_CHROME_PROFILES = ["Default", "Profile 1", "Profile 2", "Profile 3"]

def _chrome_safe_storage_pass():
    """Chrome Safe Storage 密码：每台机器随机生成、存在钥匙串里，**不能硬编码**。
    写死的话既换机即失效，也等于把本机密钥提交进仓库。取不到就返回 None。"""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "Chrome Safe Storage", "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return out.stdout.strip()


class GrokProbe(ProviderProbe):
    name = "grok"
    display_name = "Grok"

    def fetch(self):
        cookie = (self.cfg.get("cookie") or "").strip()

        # If no cookie configured, try auto-sync from Chrome
        if not cookie:
            cookie = self._auto_sync_cookie()

        if not cookie:
            return self.fail(
                "未配置 grok.com Cookie 且自动同步失败：浏览器登录 grok.com → DevTools → Network → 任一 /rest/ 请求 → "
                "复制整串 Cookie 请求头，填入 ~/.usagehub/config.json 的 providers.grok.cookie"
            )
        s = self.session()
        headers = dict(BROWSER_HEADERS)
        headers["Cookie"] = cookie

        try:
            windows = self._weekly_usage(s, cookie)
        except RuntimeError as e:
            # cookie 失效时用最新 Chrome cookie 再试一次
            if ("401" in str(e) or "403" in str(e)):
                fresh = self._auto_sync_cookie()
                if fresh and fresh != cookie:
                    cookie = fresh
                    try:
                        windows = self._weekly_usage(s, cookie)
                    except Exception as e2:
                        return self.fail("周限额查询失败: {}".format(e2))
                else:
                    return self.fail("cookie 失效（{}），请在 Chrome 重新登录 grok.com".format(e))
            else:
                return self.fail("周限额查询失败: {}".format(e))
        except Exception as e:
            return self.fail("周限额查询失败: {}".format(e))

        if not windows:
            return self.fail("未取到周限额数据")
        account, plan = self._identity(s, headers)
        return self.result(True, windows=windows, account=account, plan=plan)

    def _weekly_usage(self, s, cookie):
        """调 grok_api_v2.GrokBuildBilling/GetGrokCreditsConfig（gRPC-Web），
        取「每周 SuperGrok 限额」总用量%、各产品占比与重置时间。"""
        headers = dict(BROWSER_HEADERS)
        headers["Cookie"] = cookie
        headers["Content-Type"] = "application/grpc-web+proto"
        headers["Accept"] = "application/grpc-web+proto"
        headers["x-grpc-web"] = "1"
        resp = s.post(CREDITS_URL, headers=headers,
                      data=b"\x00\x00\x00\x00\x00", timeout=20)
        if resp.status_code in (401, 403):
            raise RuntimeError("HTTP {}".format(resp.status_code))
        if resp.status_code != 200:
            raise RuntimeError("HTTP {}: {}".format(resp.status_code, resp.text[:120]))

        msg = _grpc_web_message(resp.content)
        if msg is None:
            raise RuntimeError("gRPC-Web 响应无消息帧")
        top = _pb_parse(msg)
        cfg_bytes = _pb_first(top, 1, "bytes")
        if cfg_bytes is None:
            raise RuntimeError("响应缺 config 字段")
        cfg = _pb_parse(cfg_bytes)

        total = _pb_first(cfg, 1, "f32")            # creditUsagePercent
        reset_iso = None
        end_ts = _pb_first(cfg, 5, "bytes")         # billingPeriodEnd Timestamp
        if end_ts is not None:
            secs = _pb_first(_pb_parse(end_ts), 1, "varint")
            if secs:
                reset_iso = datetime.fromtimestamp(secs, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 各产品占比（product 枚举 → 名字）
        parts = []
        for pu_bytes in _pb_all(cfg, 7, "bytes"):
            pu = _pb_parse(pu_bytes)
            prod = _pb_first(pu, 1, "varint")
            pct = _pb_first(pu, 2, "f32")
            if pct is None:
                continue
            parts.append("{} {:g}".format(_PRODUCT_NAMES.get(prod, "产品{}".format(prod)), pct))

        label = "每周限额"
        if parts:
            label += "（{}）".format(" · ".join(parts))
        used = float(total) if total is not None else 0.0
        return [Window(label=label, remaining_pct=max(0.0, 100.0 - used),
                       resets_at=reset_iso, mergeable=False)]

    @staticmethod
    def _identity(s, headers):
        """查当前登录账号邮箱与真实订阅等级，用于展示（失败则留空，不影响额度）。"""
        account, plan = "", ""
        try:
            u = s.get(USER_URL, headers=headers, timeout=15)
            if u.status_code == 200:
                account = (u.json().get("email") or "").strip()
        except Exception:
            pass
        try:
            r = s.get(SUBS_URL, headers=headers, timeout=15)
            if r.status_code == 200:
                subs = r.json().get("subscriptions") or []
                active = [x for x in subs
                          if x.get("status") == "SUBSCRIPTION_STATUS_ACTIVE"]
                pick = (active or subs)
                if pick:
                    tier = pick[0].get("tier", "")
                    plan = _TIER_NAMES.get(tier, tier.replace("SUBSCRIPTION_TIER_", "").title())
                    trial = any(
                        (x.get("activeOffer") or {}).get("type") == "ACTIVE_OFFER_FREE_TRIAL"
                        for x in pick)
                    if trial:
                        plan += "（试用）"
        except Exception:
            pass
        return account, plan

    def _query(self, s, headers, model, kind):
        resp = s.post(RATE_URL, headers=headers,
                      data=json.dumps({"requestKind": kind, "modelName": model}),
                      timeout=20, verify=False)
        if resp.status_code in (401, 403):
            raise RuntimeError("HTTP {}（cookie 失效/被风控）".format(resp.status_code))
        if resp.status_code == 404:
            return None  # 该模型不存在，跳过
        if resp.status_code != 200:
            raise RuntimeError("HTTP {}: {}".format(resp.status_code, resp.text[:120]))
        d = resp.json()

        remaining = _first_number(d, ["remainingQueries", "remainingTokens", "remaining"])
        total = _first_number(d, ["totalQueries", "totalTokens", "total"])
        window_sec = _first_number(d, ["windowSizeSeconds"])
        label = model if kind == "DEFAULT" else "{}({})".format(model, kind)
        if window_sec:
            label += " / {}h窗口".format(int(window_sec // 3600))
        pct = (remaining / total * 100.0) if (remaining is not None and total) else None
        return Window(label=label, remaining_pct=pct, remaining_abs=remaining,
                      limit_abs=total, unit="次")

    @staticmethod
    def _auto_sync_cookie():
        """Try to read and decrypt grok.com cookies from Chrome's local SQLite database.

        Checks multiple Chrome profiles and prefers the one whose grok.com cookies
        have the most recent last_access_utc timestamp.
        Returns the cookie string, or None on failure.
        """
        if not _HAS_CRYPTO:
            return None

        safe_pass = _chrome_safe_storage_pass()
        if not safe_pass:
            return None
        # Derive decryption key via PBKDF2
        key = pbkdf2_hmac("sha1", safe_pass.encode("utf-8"), b"saltysalt", 1003, dklen=16)

        best_cookie = None
        best_ts = -1

        for profile_name in _CHROME_PROFILES:
            cookie_db = _CHROME_BASE / profile_name / "Cookies"
            if not cookie_db.exists():
                continue
            try:
                cookie_str, max_ts = _read_chrome_cookies(cookie_db, key)
                if cookie_str and max_ts > best_ts:
                    best_ts = max_ts
                    best_cookie = cookie_str
            except Exception:
                continue

        return best_cookie


def _read_chrome_cookies(cookie_db, key):
    """Read and decrypt grok.com cookies from a Chrome Cookies SQLite file.

    Returns (cookie_string, max_last_access_utc) or (None, -1) on failure.
    """
    # Copy the database to a temp file to avoid locking issues with a running Chrome
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
    try:
        os.close(tmp_fd)
        shutil.copy2(str(cookie_db), tmp_path)

        conn = sqlite3.connect("file:{}?mode=ro".format(tmp_path), uri=True)
        rows = conn.execute(
            "SELECT name, encrypted_value, last_access_utc FROM cookies "
            "WHERE host_key LIKE '%grok.com' ORDER BY last_access_utc DESC"
        ).fetchall()
        conn.close()
    except Exception:
        return None, -1
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if not rows:
        return None, -1

    pairs = []
    max_ts = -1
    for name, encrypted_value, last_access in rows:
        if last_access and last_access > max_ts:
            max_ts = last_access
        if not encrypted_value:
            continue
        try:
            value = _decrypt_chrome_cookie(encrypted_value, key)
            if value:
                pairs.append("{}={}".format(name, value))
        except Exception:
            continue

    if not pairs:
        return None, -1
    return "; ".join(pairs), max_ts


def _decrypt_chrome_cookie(encrypted_value, key):
    """Decrypt a single Chrome cookie value (macOS v10 format).

    Format: 'v10' prefix (3 bytes) + AES-CBC ciphertext.
    Key derived externally via PBKDF2. IV = 16 space bytes.
    """
    # Chrome macOS cookies start with b'v10' or b'v11'
    if len(encrypted_value) <= 3:
        return None
    # Strip the version prefix (first 3 bytes)
    ciphertext = encrypted_value[3:]
    iv = b" " * 16
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(ciphertext) + decryptor.finalize()
    # Strip PKCS7 padding
    if decrypted:
        pad_len = decrypted[-1]
        if isinstance(pad_len, int) and 1 <= pad_len <= 16:
            decrypted = decrypted[:-pad_len]
    # Skip first 32 bytes, decode utf-8
    if len(decrypted) > 32:
        return decrypted[32:].decode("utf-8", errors="replace")
    return decrypted.decode("utf-8", errors="replace")


def _first_number(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)):
            return v
    return None
