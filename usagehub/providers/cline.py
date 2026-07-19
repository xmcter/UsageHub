# -*- coding: utf-8 -*-
"""ClinePass（Cline 的开放权重模型订阅）用量限制。

ClinePass 是 $9.99/月 的订阅（转卖 DeepSeek/Qwen/Kimi/GLM/MiniMax 等），
配额按「5 小时 / 每周 / 每月」三个滚动窗口计（不是美元余额）。

接口（app.cline.bot 后台实际调用）：
  GET {base}/api/v1/users/me                     → data.email（账号）
  GET {base}/api/v1/users/me/plan/usage-limits   → data.limits[] 三个窗口的 percentUsed
认证：Authorization: Bearer <key>；key 留空时自动从 cc-switch 数据库只读提取。
"""
import json
import re
import sqlite3
from pathlib import Path

from .base import ProviderProbe
from ..core import Window

CCSWITCH_DB = Path.home() / ".cc-switch" / "cc-switch.db"


def _norm_ts(ts):
    """把 RFC3339 纳秒时间戳（...282685265Z）截成微秒，便于下游 fromisoformat 解析。"""
    if not ts or not isinstance(ts, str):
        return None
    m = re.match(r"^(.*T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$", ts)
    if not m:
        return ts
    base, frac, tz = m.group(1), m.group(2), m.group(3) or "Z"
    if frac:
        base += "." + frac[:6]
    return base + tz


# 窗口类型 → 中文标签
_LIMIT_LABELS = {
    "five_hour": "5小时窗口",
    "weekly": "每周窗口",
    "monthly": "每月窗口",
}


class ClineProbe(ProviderProbe):
    name = "cline"
    display_name = "ClinePass"

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
        account = me.get("email") or ""

        data = self._api(s, "{}/api/v1/users/me/plan/usage-limits".format(base), headers)
        limits = data.get("limits") if isinstance(data, dict) else None
        if not limits:
            return self.fail("usage-limits 返回格式无法识别: {}".format(json.dumps(data)[:200]))

        # 按 5h→周→月 固定顺序展示
        order = {"five_hour": 0, "weekly": 1, "monthly": 2}
        limits = sorted(limits, key=lambda x: order.get(x.get("type"), 9))
        windows = []
        for item in limits:
            used = item.get("percentUsed")
            if used is None:
                continue
            label = _LIMIT_LABELS.get(item.get("type"), item.get("type", "?"))
            windows.append(Window(
                label=label,
                remaining_pct=max(0.0, 100.0 - float(used)),
                resets_at=_norm_ts(item.get("resetsAt")),
                mergeable=False,  # 三个窗口是不同时间维度，勿合并
            ))
        if not windows:
            return self.fail("usage-limits 里没有可识别的窗口: {}".format(json.dumps(data)[:200]))
        return self.result(True, windows=windows, account=account, plan="Cline Pass")

    @staticmethod
    def _api(session, url, headers):
        resp = session.get(url, headers=headers, timeout=20, verify=False)
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
