# -*- coding: utf-8 -*-
"""SuperGrok（grok.com 订阅）：网页内部接口 rest/rate-limits，需要浏览器 Cookie。

注意：这是未公开接口，字段随时可能变；cookie 过期需重新粘贴。
"""
import json

from .base import ProviderProbe
from ..core import Window

RATE_URL = "https://grok.com/rest/rate-limits"

BROWSER_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://grok.com",
    "Referer": "https://grok.com/",
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "*/*",
}


class GrokProbe(ProviderProbe):
    name = "grok"
    display_name = "SuperGrok"

    def fetch(self):
        cookie = (self.cfg.get("cookie") or "").strip()
        if not cookie:
            return self.fail(
                "未配置 grok.com Cookie：浏览器登录 grok.com → DevTools → Network → 任一 /rest/ 请求 → "
                "复制整串 Cookie 请求头，填入 ~/.usagehub/config.json 的 providers.grok.cookie"
            )
        models = self.cfg.get("models") or ["grok-4", "grok-3"]
        kinds = self.cfg.get("request_kinds") or ["DEFAULT"]
        s = self.session()
        headers = dict(BROWSER_HEADERS)
        headers["Cookie"] = cookie

        windows = []
        errors = []
        for model in models:
            for kind in kinds:
                try:
                    w = self._query(s, headers, model, kind)
                    if w:
                        windows.append(w)
                except Exception as e:
                    errors.append("{}[{}]: {}".format(model, kind, e))
        if windows:
            return self.result(True, windows=windows,
                               error="; ".join(errors) if errors else "")
        return self.fail("全部查询失败（cookie 可能过期或被 Cloudflare 拦截）: " + "; ".join(errors)[:300])

    def _query(self, s, headers, model, kind):
        resp = s.post(RATE_URL, headers=headers,
                      data=json.dumps({"requestKind": kind, "modelName": model}),
                      timeout=20)
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


def _first_number(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)):
            return v
    return None
