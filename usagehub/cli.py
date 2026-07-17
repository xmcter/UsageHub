# -*- coding: utf-8 -*-
"""CLI 入口：usagehub [--json] [--providers a,b] / usagehub serve [--lan]"""
import argparse
import json
import sys
from datetime import datetime, timezone

from .config import load_config, CONFIG_PATH
from .core import run_probes
from .providers import build_probes, ALL_PROBES


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="usagehub",
        description="统一查看 AI 订阅剩余余额（Claude / ClinePass / SuperGrok / Antigravity）",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--providers", help="只查这些（逗号分隔）: {}".format(",".join(ALL_PROBES)))
    sub = parser.add_subparsers(dest="cmd")
    sv = sub.add_parser("serve", help="启动本地 Web 面板")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8787)
    sv.add_argument("--lan", action="store_true", help="绑 0.0.0.0，手机浏览器可通过局域网访问")
    args = parser.parse_args(argv)

    cfg = load_config()

    if args.cmd == "serve":
        from .server import serve
        host = "0.0.0.0" if args.lan else args.host
        serve(cfg, host, args.port)
        return 0

    only = [p.strip() for p in args.providers.split(",")] if args.providers else None
    if only:
        bad = [p for p in only if p not in ALL_PROBES]
        if bad:
            parser.error("未知 provider: {}（可选: {}）".format(",".join(bad), ",".join(ALL_PROBES)))
    results = run_probes(build_probes(cfg, only))

    if args.json:
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))
    else:
        print_table(results)
        print("\n配置文件: {}".format(CONFIG_PATH))
    return 0


def print_table(results):
    now = datetime.now(timezone.utc)
    for r in results:
        mark = "✅" if r.ok else "❌"
        head = "{} {}".format(mark, r.display_name)
        extra = " · ".join(x for x in (r.plan, r.account) if x)
        if extra:
            head += "（{}）".format(extra)
        print(head)
        if not r.ok:
            for line in r.error.splitlines():
                print("   {}".format(line))
            continue
        for w in r.windows:
            parts = []
            if w.remaining_pct is not None:
                parts.append("剩余 {:>5.1f}%".format(w.remaining_pct))
            if w.remaining_abs is not None:
                rng = "{:g}/{:g}".format(w.remaining_abs, w.limit_abs) if w.limit_abs \
                    else "{:g}".format(w.remaining_abs)
                parts.append("${}".format(rng) if w.unit == "$" else
                             "{} {}".format(rng, w.unit).strip())
            if w.resets_at:
                cd = _countdown(w.resets_at, now)
                if cd:
                    parts.append("重置于 {} 后".format(cd))
            bar = _bar(w.remaining_pct)
            print("   {:<24} {} {}".format(w.label, bar, "  ".join(parts)))
        if r.error:  # 部分成功时的警告
            print("   ⚠ {}".format(r.error))


def _bar(pct, width=20):
    if pct is None:
        return " " * (width + 2)
    filled = int(round(pct / 100.0 * width))
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def _countdown(resets_at, now):
    try:
        t = datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
        delta = t - now
        secs = int(delta.total_seconds())
        if secs <= 0:
            return None
        h, rem = divmod(secs, 3600)
        m = rem // 60
        return "{}h{:02d}m".format(h, m) if h else "{}m".format(m)
    except Exception:
        return None


if __name__ == "__main__":
    sys.exit(main())
