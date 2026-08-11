# -*- coding: utf-8 -*-
"""MonoCloud（mymonocloud.com 面板）订阅额度探测。

面板是 Inertia/Laravel 应用：登录后请求页面路由（带 session cookie），
响应里 `props` 含订单/服务数据，其中包含流量/时间额度。

数据源（实测 2026-08-10）：
  GET /                → props.activeServices: [{id, name, status}]（套餐列表）
  GET /services/{id}   → props.service / props.plan / props.bandwidth / props.dataPack
    bandwidth = {upload, download, allowance, resetsAt}
      upload+download = 已用流量；allowance = 当前总可用（含已购流量包）
    dataPack = {baseAllowance, packAvailableBytes, packPurchasedBytes}（额外流量包）
    plan.dataAllowance = 套餐原始额度

凭据（多套餐 = 多账号，各自一条）：
  1. 自动：Chrome 里已登录 mymonocloud.com 时，自动解密提取 cookie（零配置）；
  2. 手动：config.providers.monocloud.accounts 填
     {"email": "...", "session": "monocloud_session=...; xsrf=..."}
     （浏览器 DevTools 复制 Cookie 请求头，或 `usagehub auth monocloud` 写入）。

面板字段为私有实现，解析尽量宽容：数值取不到就不展示，
解析失败给出原始片段方便迭代。
"""
import json
import re
from datetime import datetime, timezone

from .base import ProviderProbe
from .chrome_cookies import read_chrome_cookies
from ..core import Window

BASE_URL = "https://mymonocloud.com"
COOKIE_HOST_FILTER = "%monocloud.com"


class MonoCloudProbe(ProviderProbe):
    name = "monocloud"
    display_name = "MonoCloud"

    def __init__(self, cfg, global_cfg, email="", session=""):
        super().__init__(cfg, global_cfg)
        self.email = email
        self._cookie = session

    @classmethod
    def create_probes(cls, cfg, global_cfg):
        accounts = cfg.get("accounts") or []
        probes = []
        seen = set()
        for acc in accounts:
            email = (acc.get("email") or "").strip().lower()
            if email in seen:
                continue
            seen.add(email)
            probes.append(cls(cfg, global_cfg, email=email, session=acc.get("session") or ""))
        # 自动提取的 Chrome 会话作为兜底账号（未配置账号时生效）
        if not accounts:
            probes.append(cls(cfg, global_cfg))
        return probes

    def fetch(self):
        session = self._cookie
        if not session:
            # 尝试从 Chrome 自动提取
            session, _ = read_chrome_cookies(COOKIE_HOST_FILTER)
        if not session:
            return self.fail(
                "未找到 MonoCloud 登录会话。请先在 Chrome 登录 mymonocloud.com（面板右上角登录），"
                "或在 config.providers.monocloud.accounts 填写 email 与 session cookie。"
            )
        base = (self.cfg.get("base_url") or BASE_URL).rstrip("/")
        s = self.session(use_proxy=True)
        headers = {
            "Cookie": session,
            "Accept": "application/json",
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"),
        }

        # 1. 首页拿套餐列表（activeServices）
        home = self._get(s, base + "/", headers)
        if home is None:
            return self.fail("登录会话无效或已过期，请重新在 Chrome 登录 mymonocloud.com")
        services = self._extract_services(home)

        if not services:
            sample = json.dumps(home, ensure_ascii=False)[:300]
            return self.fail("已登录但未识别到服务列表: {}".format(sample))

        # 2. 逐个请求 /services/{id} 详情，解析每个套餐的额度
        windows = []
        for svc in services:
            page = self._get(s, "{}/services/{}".format(base, svc.get("id")), headers)
            if page is None:
                continue
            w = self._service_window(page)
            if w:
                windows.append(w)

        if not windows:
            return self.fail("已登录但服务详情里没有额度字段: {}".format(
                json.dumps(home, ensure_ascii=False)[:300]))

        account = self.email or self._account_name(home) or "monocloud"
        return self.result(True, windows=windows, account=account,
                           plan="MonoCloud 面板")

    # ---- 内部 ----
    def _get(self, session, url, headers):
        try:
            resp = session.get(url, headers=headers, timeout=20)
        except Exception:
            return None
        if resp.status_code != 200:
            return None
        ct = resp.headers.get("Content-Type", "")
        if "json" in ct:
            try:
                return resp.json()
            except Exception:
                return None
        # HTML 里的 Inertia data-page
        m = re.search(r'<script data-page="[^"]*" type="application/json">(.*?)</script>',
                      resp.text, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                return None
        return None

    def _extract_services(self, page):
        """从 Inertia 页面数据里取 activeServices 列表。"""
        found = []
        if not isinstance(page, dict):
            return found
        props = page.get("props") if isinstance(page.get("props"), dict) else page
        v = props.get("activeServices")
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict) and item.get("id"):
                    found.append(item)
        return found

    @staticmethod
    def _account_name(page):
        """从 Inertia props 里取登录账号名（用于卡片标题）。"""
        try:
            props = page.get("props") or {}
            auth = props.get("auth") or {}
            user = auth.get("user") or {}
            return user.get("email") or user.get("name") or ""
        except Exception:
            return ""

    def _service_window(self, page):
        """从单个服务详情页提取额度窗口（bandwidth + dataPack）。"""
        props = page.get("props") if isinstance(page.get("props"), dict) else page
        svc = props.get("service") or {}
        plan = props.get("plan") or {}
        bw = props.get("bandwidth") or {}
        dp = props.get("dataPack") or {}

        name = (plan.get("name") or svc.get("name") or svc.get("service_name")
                or "套餐")

        # 已用 = upload + download（字节）
        used_bytes = None
        if isinstance(bw.get("upload"), (int, float)) and isinstance(bw.get("download"), (int, float)):
            used_bytes = float(bw.get("upload")) + float(bw.get("download"))
        # 总额 = allowance（当前总可用，含已购流量包）；兜底 dataAllowance
        limit_bytes = bw.get("allowance") if isinstance(bw.get("allowance"), (int, float)) else None
        if limit_bytes is None:
            limit_bytes = plan.get("dataAllowance") if isinstance(plan.get("dataAllowance"), (int, float)) else None
        # 额外流量包（data pack）
        pack_bytes = dp.get("packAvailableBytes") if isinstance(dp.get("packAvailableBytes"), (int, float)) else None

        resets_at = self._iso(bw.get("resetsAt") or svc.get("expireDate"))

        if used_bytes is None and limit_bytes is None and not resets_at:
            return None

        # 换算成 GB 展示（保留 1 位）
        def to_gb(b):
            return round(b / 1024.0 ** 3, 1) if b is not None else None

        used_gb = to_gb(used_bytes)
        limit_gb = to_gb(limit_bytes)
        pack_gb = to_gb(pack_bytes)

        remaining_pct = None
        if limit_bytes is not None and limit_bytes > 0:
            used_v = used_bytes if used_bytes is not None else 0
            remaining_pct = max(0.0, min(100.0, 100.0 * (limit_bytes - used_v) / limit_bytes))

        label = str(name)
        if pack_gb:
            label += "（含流量包 {:.1f}GB）".format(pack_gb)

        return Window(
            label=label,
            remaining_pct=remaining_pct,
            remaining_abs=used_gb,      # 已用 GB（绝对值展示用）
            limit_abs=limit_gb,          # 总量 GB
            unit="GB",
            resets_at=resets_at,
            mergeable=False,
        )

    @staticmethod
    def _iso(v):
        if not v:
            return None
        v = str(v)
        # ISO 直接可用（面板返回的是日期 "2026-08-28"）
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass
        try:
            ts = float(v)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return None
