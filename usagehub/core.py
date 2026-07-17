# -*- coding: utf-8 -*-
"""核心数据模型与探测调度。"""
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Optional

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

PROBE_TIMEOUT = 30  # 单个 provider 的总超时（秒）


@dataclass
class Window:
    """一个限额窗口（如 Claude 5 小时窗、Antigravity 某模型额度、Cline 余额）。"""
    label: str
    remaining_pct: Optional[float] = None   # 剩余百分比 0-100
    remaining_abs: Optional[float] = None   # 剩余绝对值
    limit_abs: Optional[float] = None       # 总额度绝对值
    unit: str = ""                          # 绝对值单位，如 "$"、"次"、"credits"
    resets_at: Optional[str] = None         # ISO 时间字符串（UTC），无重置概念则为 None


@dataclass
class ProbeResult:
    provider: str
    display_name: str
    ok: bool
    plan: str = ""
    account: str = ""
    error: str = ""
    windows: List[Window] = field(default_factory=list)
    fetched_at: str = ""

    def critical_pct(self) -> float:
        """最紧张窗口的剩余百分比，用于排序；无数据按 999 排最后。"""
        pcts = [w.remaining_pct for w in self.windows if w.remaining_pct is not None]
        if not self.ok:
            return 1000
        return min(pcts) if pcts else 999

    def to_dict(self) -> dict:
        return asdict(self)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_probes(probes) -> List[ProbeResult]:
    """并发跑所有 provider，任一家失败不影响其他家。"""
    results = []
    with ThreadPoolExecutor(max_workers=max(len(probes), 1)) as pool:
        futs = {pool.submit(_safe_fetch, p): p for p in probes}
        for fut in as_completed(futs):
            results.append(fut.result())
    results.sort(key=lambda r: r.critical_pct())
    return results


def _safe_fetch(probe) -> ProbeResult:
    try:
        return probe.fetch()
    except Exception as e:  # 兜底：探测代码抛出的任何异常都转为失败结果
        return ProbeResult(
            provider=probe.name,
            display_name=probe.display_name,
            ok=False,
            error="{}: {}".format(type(e).__name__, e),
            fetched_at=utcnow_iso(),
        )
