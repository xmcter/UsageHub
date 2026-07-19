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
    mergeable: bool = True                  # 是否允许与同状态窗口合并展示（同配额池的多模型才该合）


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

    # 区分成功与失败记录
    successes = [r for r in results if r.ok]
    failures = [r for r in results if not r.ok]

    # 对成功记录去重
    seen_success = set()
    deduped_successes = []
    for r in successes:
        key = (r.provider, r.account)
        if key in seen_success:
            continue
        seen_success.add(key)
        deduped_successes.append(r)

    # 过滤失败记录：如果同一 provider 已有成功记录且失败记录没有账号（如残留进程探测失败），则舍弃
    deduped_failures = []
    success_providers = {r.provider for r in deduped_successes}
    for r in failures:
        if r.provider in success_providers and not r.account:
            continue
        if (r.provider, r.account) in seen_success:
            continue
        deduped_failures.append(r)

    deduped = deduped_successes + deduped_failures
    
    # 对具有完全相同限额状态的关联模型/窗口进行合并展示，避免一屏展示过多冗余行
    for r in deduped:
        r.windows = merge_window_labels(r.windows)
        
    deduped.sort(key=lambda r: r.critical_pct())
    return deduped


def merge_window_labels(windows: List[Window]) -> List[Window]:
    if not windows:
        return []
    
    # 不可合并的窗口（如 ClinePass 的 5h/周/月 是不同时间维度）原样保留
    passthrough = [w for w in windows if not w.mergeable]
    windows = [w for w in windows if w.mergeable]

    # 按照用量状态分组
    groups = {}
    for w in windows:
        pct_key = round(w.remaining_pct, 4) if w.remaining_pct is not None else None
        key = (pct_key, w.remaining_abs, w.limit_abs, w.unit, w.resets_at)
        if key not in groups:
            groups[key] = []
        groups[key].append(w)
        
    merged_windows = []
    for key, group_windows in groups.items():
        if len(group_windows) == 1:
            merged_windows.append(group_windows[0])
            continue
            
        # 合并具有相同前缀但括号内后缀不同的标签 (如 Model (Low) + Model (High) -> Model (Low/High))
        base_to_suffixes = {}
        others = []
        for w in group_windows:
            label = w.label.strip()
            if "(" in label and label.endswith(")"):
                parts = label.rsplit("(", 1)
                base = parts[0].strip()
                suffix = parts[1][:-1].strip()
                if base not in base_to_suffixes:
                    base_to_suffixes[base] = []
                if suffix not in base_to_suffixes[base]:
                    base_to_suffixes[base].append(suffix)
            else:
                others.append(label)
                
        merged_labels = []
        for base, suffixes in base_to_suffixes.items():
            if len(suffixes) == 1:
                merged_labels.append(f"{base} ({suffixes[0]})")
            else:
                merged_labels.append(f"{base} ({'/'.join(suffixes)})")
        merged_labels.extend(others)
        
        # 将不同的基础模型以 " / " 串联
        new_label = " / ".join(merged_labels)
        
        first = group_windows[0]
        merged_windows.append(Window(
            label=new_label,
            remaining_pct=first.remaining_pct,
            remaining_abs=first.remaining_abs,
            limit_abs=first.limit_abs,
            unit=first.unit,
            resets_at=first.resets_at
        ))
    return passthrough + merged_windows


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
