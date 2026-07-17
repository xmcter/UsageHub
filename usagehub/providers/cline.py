# -*- coding: utf-8 -*-
"""ClinePass（= Cline 官方账户体系）credits 余额。

接口（源自 cline/cline 仓库 ClineAccountService）：
  GET {base}/api/v1/users/me            → data.uid
  GET {base}/api/v1/users/{uid}/balance → data.balance（单位：美分）
认证：Authorization: Bearer <key>；key 留空时自动从 cc-switch 数据库只读提取。
"""
import json
import re
import sqlite3
from pathlib import Path

from .base import ProviderProbe
from ..core import Window

CCSWITCH_DB = Path.home() / ".cc-switch" / "cc-switch.db"


class ClineProbe(ProviderProbe):
    name = "cline"
    display_name = "ClinePass (Cline)"

    def fetch(self):
        key = (self.cfg.get("api_key") or "").strip() or self._key_from_ccswitch()
        if not key:
            return self.fail(
                "未找到 Cline API key：在 ~/.usagehub/config.json 的 providers.cline.api_key 填入，"
                "或确认 cc-switch 里有 ClinePass 配置"
            )
        base = (self.cfg.get("base_url") or "https://api.cline.bot").rstrip("/")
        s = self.session()
        headers = {"Authorization": "Bearer {}".format(key), "Content-Type": "application/json"}

        me = self._api(s, "{}/api/v1/users/me".format(base), headers)
        uid = me.get("uid") or me.get("id")
        if not uid:
            return self.fail("users/me 返回中没有 uid: {}".format(json.dumps(me)[:200]))

        bal = self._api(s, "{}/api/v1/users/{}/balance".format(base, uid), headers)
        cents = bal.get("balance")
        if cents is None:
            return self.fail("balance 返回格式无法识别: {}".format(json.dumps(bal)[:200]))
        dollars = float(cents) / 100.0

        account = me.get("email") or ""
        win = Window(label="Credits 余额", remaining_abs=round(dollars, 2), unit="$")
        return self.result(True, windows=[win], account=account,
                           plan=str(me.get("subscription") or me.get("plan") or ""))

    @staticmethod
    def _api(session, url, headers):
        resp = session.get(url, headers=headers, timeout=20)
        if resp.status_code == 401:
            raise RuntimeError("Cline key 无效或过期（401）")
        if resp.status_code != 200:
            raise RuntimeError("HTTP {} {}: {}".format(resp.status_code, url, resp.text[:150]))
        body = resp.json()
        # 官方响应封套 {success, error, data}
        if isinstance(body, dict) and "data" in body:
            if body.get("success") is False:
                raise RuntimeError("API error: {}".format(body.get("error")))
            return body["data"] or {}
        return body

    @staticmethod
    def _key_from_ccswitch():
        """从 cc-switch.db 只读提取 ClinePass 的 key（不修改任何数据）。"""
        if not CCSWITCH_DB.exists():
            return None
        try:
            conn = sqlite3.connect("file:{}?mode=ro".format(CCSWITCH_DB), uri=True)
            rows = conn.execute(
                "select settings_config from providers where id like '%cline%' or name like '%Cline%'"
            ).fetchall()
            conn.close()
        except Exception:
            return None
        for (raw,) in rows:
            if not raw:
                continue
            # 在 JSON 里找 key 形态的值（cline 的 key 一般是长 token 串）
            for m in re.finditer(r'"(?:apiKey|api_key|ANTHROPIC_AUTH_TOKEN|ANTHROPIC_API_KEY|OPENAI_API_KEY)"\s*:\s*"([^"]{20,})"', raw):
                return m.group(1)
        return None
