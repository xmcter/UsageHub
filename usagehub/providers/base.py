# -*- coding: utf-8 -*-
"""ProviderProbe 基类（沿用 CodexBarWindows 的插件式设计）。"""
import requests

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
