# -*- coding: utf-8 -*-
"""Provider 注册表：新增订阅 = 新增一个 Probe 子类 + 挂到这里。"""
from .claude import ClaudeProbe
from .cline import ClineProbe
from .grok import GrokProbe
from .antigravity import AntigravityProbe

ALL_PROBES = {
    "claude": ClaudeProbe,
    "cline": ClineProbe,
    "grok": GrokProbe,
    "antigravity": AntigravityProbe,
}


def build_probes(config: dict, only=None):
    """按配置实例化启用的 provider；only 为名称列表时只跑这些。"""
    probes = []
    pcfg = config.get("providers", {})
    for name, cls in ALL_PROBES.items():
        c = pcfg.get(name, {})
        if only is not None:
            if name in only:
                probes.append(cls(c, config))
        elif c.get("enabled", True):
            probes.append(cls(c, config))
    return probes
