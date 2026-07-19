# -*- coding: utf-8 -*-
"""Claude 订阅（Pro/Max）：官方 OAuth usage 接口，含 5 小时窗口与周窗口。

打法同 ClaudeBar / ccusage：用 Claude Code 的 OAuth token 调
GET https://api.anthropic.com/api/oauth/usage

也支持从 Chrome 浏览器提取 sessionKey cookie，走
GET https://claude.ai/api/usage
"""
import json
import os
import platform
import sqlite3
import subprocess
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

from .base import ProviderProbe
from ..core import Window

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
BROWSER_ORGS_URL = "https://claude.ai/api/organizations"
BROWSER_USAGE_URL = "https://claude.ai/api/organizations/{org_uuid}/usage"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

WINDOW_LABELS = {
    "five_hour": "5小时窗口",
    "seven_day": "周窗口(整体)",
    "seven_day_sonnet": "周窗口(Sonnet)",
    "seven_day_opus": "周窗口(Opus)",
    "seven_day_oauth_apps": "周窗口(OAuth Apps)",
}


class ClaudeProbe(ProviderProbe):
    name = "claude"
    display_name = "Claude 订阅"

    def fetch(self):
        token = self._find_token()
        if not token:
            return self.fail(
                "未找到 Claude Code OAuth 凭据。任选其一：\n"
                "1) 终端跑 `claude setup-token` 生成长期 token，填入 ~/.usagehub/config.json 的 providers.claude.oauth_token；\n"
                "2) 用 `claude /login` 登录官方账号（会写入钥匙串/.credentials.json）\n"
                "3) 用 Chrome 登录 claude.ai（会自动从浏览器 Cookie 提取 sessionKey）"
            )
        is_session = token.startswith("sk-ant-sid")
        if is_session:
            s = self.session()
            headers = {
                "Cookie": "sessionKey={}".format(token),
                "User-Agent": BROWSER_UA,
            }
            orgs_resp = s.get(BROWSER_ORGS_URL, headers=headers, timeout=20)
            if orgs_resp.status_code == 401:
                return self.fail("浏览器 sessionKey 已失效（401），请在 Chrome 重新登录 claude.ai")
            if orgs_resp.status_code != 200:
                return self.fail("获取组织失败 HTTP {}: {}".format(
                    orgs_resp.status_code, orgs_resp.text[:200]))
            orgs = orgs_resp.json()
            if not orgs:
                return self.fail("sessionKey 有效但账号下没有组织")
            resp = s.get(
                BROWSER_USAGE_URL.format(org_uuid=orgs[0]["uuid"]),
                headers=headers,
                timeout=20,
            )
        else:
            s = self.session()
            resp = s.get(
                USAGE_URL,
                headers={
                    "Authorization": "Bearer {}".format(token),
                    "anthropic-beta": "oauth-2025-04-20",
                    "Content-Type": "application/json",
                },
                timeout=20,
            )
        if resp.status_code == 401:
            return self.fail("OAuth token 已失效（401），请重新 `claude setup-token` 或 `claude /login`")
        if resp.status_code != 200:
            return self.fail("HTTP {}: {}".format(resp.status_code, resp.text[:200]))
        data = resp.json()

        windows = []
        for key, label in WINDOW_LABELS.items():
            w = self._parse_window(data.get(key), label)
            if w:
                windows.append(w)
        # 防御：接口若新增/改名窗口字段，凡带 utilization 的对象都收进来
        known = set(WINDOW_LABELS.keys())
        for key, val in data.items():
            if key not in known and isinstance(val, dict) and "utilization" in val:
                w = self._parse_window(val, key)
                if w:
                    windows.append(w)
        if not windows:
            return self.fail("接口返回格式无法识别: {}".format(json.dumps(data)[:200]))
        return self.result(True, windows=windows)

    @staticmethod
    def _parse_window(obj, label):
        if not isinstance(obj, dict):
            return None
        util = obj.get("utilization")
        if util is None:
            return None
        return Window(
            label=label,
            remaining_pct=max(0.0, 100.0 - float(util)),
            resets_at=obj.get("resets_at"),
        )

    # ---- token 发现链 ----
    def _find_token(self):
        tok = (self.cfg.get("oauth_token") or "").strip()
        if tok:
            return tok
        tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
        if tok:
            return tok
        if platform.system() == "Darwin":
            tok = self._from_keychain()
            if tok:
                return tok
        tok = self._from_credentials_file()
        if tok:
            return tok
        if platform.system() == "Darwin":
            return self._from_browser_session()
        return None

    @staticmethod
    def _from_keychain():
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=10,
            )
            if out.returncode != 0:
                return None
            return ClaudeProbe._extract_access_token(out.stdout.strip())
        except Exception:
            return None

    @staticmethod
    def _from_credentials_file():
        p = Path.home() / ".claude" / ".credentials.json"
        if not p.exists():
            return None
        try:
            return ClaudeProbe._extract_access_token(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    @staticmethod
    def _extract_access_token(raw):
        try:
            d = json.loads(raw)
        except Exception:
            return raw or None  # 有些用户直接存裸 token
        oauth = d.get("claudeAiOauth") or d
        return oauth.get("accessToken")

    @staticmethod
    def _chrome_profiles():
        """Chrome 的 Cookies 库按 profile 分开存，登录哪个 profile 就在哪个库里。
        用户常用的是 Default 而非 Profile 1，所以全都扫一遍，按最近修改优先。"""
        base = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
        if not base.is_dir():
            return []
        found = []
        for d in base.iterdir():
            if not d.is_dir() or d.name == "System Profile":
                continue
            if d.name != "Default" and not d.name.startswith("Profile "):
                continue
            c = d / "Cookies"
            if c.exists():
                found.append(c)
        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return found

    @staticmethod
    def _chrome_safe_storage_key():
        """Safe Storage 密码是每台机器随机生成、存在钥匙串里的，不能写死在源码里。"""
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", "Chrome Safe Storage", "-w"],
                capture_output=True, text=True, timeout=10,
            )
            if out.returncode != 0 or not out.stdout.strip():
                return None
            chrome_pass = out.stdout.strip().encode()
        except Exception:
            return None
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA1(),
            length=16,
            salt=b"saltysalt",
            iterations=1003,
        )
        return kdf.derive(chrome_pass)

    @staticmethod
    def _from_browser_session():
        """从 Chrome 各 profile 的 Cookies 数据库提取 claude.ai 的 sessionKey。"""
        if not HAS_CRYPTO:
            return None
        key = ClaudeProbe._chrome_safe_storage_key()
        if key is None:
            return None
        import tempfile
        import shutil
        for cookies_path in ClaudeProbe._chrome_profiles():
            try:
                # 复制数据库到临时文件避免锁冲突
                tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
                try:
                    os.close(tmp_fd)
                    shutil.copy2(str(cookies_path), tmp_path)
                    conn = sqlite3.connect("file:{}?mode=ro".format(tmp_path), uri=True)
                    try:
                        cursor = conn.execute(
                            "SELECT encrypted_value FROM cookies "
                            "WHERE host_key = '.claude.ai' AND name = 'sessionKey' "
                            "ORDER BY last_access_utc DESC LIMIT 1"
                        )
                        row = cursor.fetchone()
                    finally:
                        conn.close()
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                if not row or not row[0]:
                    continue
                # 去掉前 3 字节标识（v10 / v11），AES-CBC 解密
                encrypted_value = row[0][3:]
                cipher = Cipher(algorithms.AES(key), modes.CBC(b" " * 16))
                decryptor = cipher.decryptor()
                decrypted = decryptor.update(encrypted_value) + decryptor.finalize()
                # 去 PKCS7 padding，跳过前 32 字节
                pad_len = decrypted[-1]
                decrypted = decrypted[:-pad_len]
                tok = decrypted[32:].decode("utf-8")
                if tok:
                    return tok
            except Exception:
                continue
        return None
