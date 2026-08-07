# -*- coding: utf-8 -*-
"""Command Code 用量限制与账号额度。

API：
  GET https://api.commandcode.ai/alpha/whoami                    → user.userName / user.email
  GET https://api.commandcode.ai/alpha/billing/subscriptions    → data.planId, data.status
  GET https://api.commandcode.ai/alpha/billing/credits          → windowLimits (fiveHour, weekly), credits

凭据提取顺序：
  1. config.json providers.commandcode.api_key
  2. ~/.commandcode/auth.json (apiKey)
"""
from datetime import datetime, timezone
import json
from pathlib import Path

from .base import ProviderProbe
from ..core import Window

COMMANDCODE_AUTH = Path.home() / ".commandcode" / "auth.json"

_PLAN_NAMES = {
    "individual-go": "Individual Go",
    "individual-max": "Individual Max",
    "individual-ultra": "Individual Ultra",
    "teams-pro": "Teams Pro",
}

_LIMIT_LABELS = {
    "fiveHour": "5小时窗口",
    "weekly": "每周窗口",
}


def _ms_to_iso(ms):
    if not ms or not isinstance(ms, (int, float)):
        return None
    try:
        dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


class CommandCodeProbe(ProviderProbe):
    name = "commandcode"
    display_name = "Command Code"

    def fetch(self):
        key, auto_account = self._get_auth()
        if not key:
            return self.fail(
                "未找到 Command Code API Key：请在 ~/.usagehub/config.json 的 providers.commandcode.api_key 填入，"
                "或登录 Command Code CLI（~/.commandcode/auth.json）"
            )
        base = (self.cfg.get("base_url") or "https://api.commandcode.ai").rstrip("/")
        s = self.session()
        headers = {
            "Authorization": "Bearer {}".format(key),
            "Content-Type": "application/json",
            "User-Agent": "command-code/1.14.1",
        }

        # 1. 账号标识
        account = auto_account
        try:
            who = self._api(s, "{}/alpha/whoami".format(base), headers)
            user = who.get("user") or {}
            account = user.get("userName") or user.get("email") or account
        except Exception:
            pass

        # 2. 订阅计划名
        plan_name = "Command Code"
        try:
            sub = self._api(s, "{}/alpha/billing/subscriptions".format(base), headers)
            sub_data = sub.get("data") if isinstance(sub, dict) else {}
            if isinstance(sub_data, dict):
                pid = sub_data.get("planId") or ""
                plan_name = _PLAN_NAMES.get(pid, pid or "Command Code")
        except Exception:
            pass

        # 3. 窗口与额度
        cred_res = self._api(s, "{}/alpha/billing/credits".format(base), headers)
        limits = cred_res.get("windowLimits") if isinstance(cred_res, dict) else {}
        if not isinstance(limits, dict):
            return self.fail("billing/credits 返回格式无法识别: {}".format(json.dumps(cred_res)[:200]))

        windows = []
        for wkey in ("fiveHour", "weekly"):
            wdata = limits.get(wkey)
            if not isinstance(wdata, dict):
                continue
            used = wdata.get("used")
            cap = wdata.get("cap")
            if used is None or cap is None or cap <= 0:
                continue
            used_val = float(used)
            cap_val = float(cap)
            pct_used = min(100.0, max(0.0, (used_val / cap_val) * 100.0))
            remaining_pct = max(0.0, 100.0 - pct_used)
            resets_at = _ms_to_iso(wdata.get("resetAt"))
            label = _LIMIT_LABELS.get(wkey, wkey)

            windows.append(Window(
                label=label,
                remaining_pct=remaining_pct,
                resets_at=resets_at,
                mergeable=False,
            ))

        if not windows:
            return self.fail("billing/credits 里没有可识别的限额窗口: {}".format(json.dumps(cred_res)[:200]))

        return self.result(True, windows=windows, account=account or "Command Code", plan=plan_name)

    @staticmethod
    def _api(session, url, headers):
        resp = session.get(url, headers=headers, timeout=20)
        if resp.status_code == 401:
            raise RuntimeError("Command Code API Key 无效或过期（401）")
        if resp.status_code != 200:
            raise RuntimeError("HTTP {} {}: {}".format(resp.status_code, url, resp.text[:150]))
        return resp.json()

    def _get_auth(self):
        key = (self.cfg.get("api_key") or "").strip()
        account = ""
        if key:
            return key, account
        if COMMANDCODE_AUTH.exists():
            try:
                with open(COMMANDCODE_AUTH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    key = (data.get("apiKey") or "").strip()
                    account = data.get("userName") or data.get("userId") or ""
            except Exception:
                pass
        return key, account
