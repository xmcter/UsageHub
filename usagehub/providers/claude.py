# -*- coding: utf-8 -*-
"""Claude 订阅（Pro/Max）：官方 OAuth usage 接口，含 5 小时窗口与周窗口。

打法同 ClaudeBar / ccusage：用 Claude Code 的 OAuth token 调
GET https://api.anthropic.com/api/oauth/usage
"""
import json
import os
import platform
import subprocess
from pathlib import Path

from .base import ProviderProbe
from ..core import Window

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

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
                "2) 用 `claude /login` 登录官方账号（会写入钥匙串/.credentials.json）"
            )
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
        return self._from_credentials_file()

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
