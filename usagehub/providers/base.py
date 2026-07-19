# -*- coding: utf-8 -*-
"""ProviderProbe 基类（沿用 CodexBarWindows 的插件式设计）。"""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..core import ProbeResult, utcnow_iso


class ProviderProbe:
    name = "base"
    display_name = "Base"

    def __init__(self, cfg: dict, global_cfg: dict):
        self.cfg = cfg or {}
        self.global_cfg = global_cfg or {}

    # ---- 子类实现 ----
    def fetch(self) -> ProbeResult:
        raise NotImplementedError

    # ---- 公共工具 ----
    def session(self, use_proxy: bool = True) -> requests.Session:
        s = requests.Session()
        proxy = (self.global_cfg.get("proxy") or "").strip()
        if use_proxy and proxy:
            s.proxies = {"http": proxy, "https": proxy}
        if use_proxy:
            # 本地代理（Clash）偶发掐断连接，对 GET 做瞬时错误重试
            retry = Retry(
                total=3, connect=3, read=2,
                backoff_factor=0.5,
                status_forcelist=(502, 503, 504),
                allowed_methods=("GET",),
            )
            adapter = HTTPAdapter(max_retries=retry)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
        if not use_proxy:
            s.trust_env = False  # 本地接口（如 Antigravity）绕过系统/环境代理
        return s

    def result(self, ok: bool, **kw) -> ProbeResult:
        return ProbeResult(
            provider=self.name,
            display_name=self.display_name,
            ok=ok,
            fetched_at=utcnow_iso(),
            **kw
        )

    def fail(self, error: str) -> ProbeResult:
        return self.result(False, error=error)
