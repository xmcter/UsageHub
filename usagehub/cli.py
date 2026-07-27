# -*- coding: utf-8 -*-
"""CLI 入口：usagehub [--json] [--providers a,b] / usagehub serve [--lan]"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

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

    sub.add_parser("menu", help="启动 macOS 状态栏常驻程序")
    au = sub.add_parser("auth", help="浏览器登录添加账号（不依赖 CodexBar）")
    au_sub = au.add_subparsers(dest="auth_provider")
    au_ag = au_sub.add_parser("antigravity", help="浏览器 Google 登录 Antigravity 账号")
    au_ag.add_argument("--timeout", type=int, default=300, help="等待授权秒数，默认 300")

    sn = sub.add_parser("snapshot", help="生成端到端加密的静态快照页（可推到常开静态站）")
    sn.add_argument("--out", default=str(Path.home() / ".usagehub" / "cloud"),
                    help="输出目录，默认 ~/.usagehub/cloud")
    sn.add_argument("--deploy", action="store_true", help="生成后用 vercel --prod 部署")

    nt = sub.add_parser("notify", help="检查额度是否恢复，恢复了就发邮件")
    nt.add_argument("--dry-run", action="store_true", help="只检查并打印，不发邮件、不更新状态")
    nt.add_argument("--test", action="store_true", help="发一封测试邮件，验证 SMTP 配置")
    args = parser.parse_args(argv)

    cfg = load_config()

    if args.cmd == "serve":
        from .server import serve
        host = "0.0.0.0" if args.lan else args.host
        serve(cfg, host, args.port)
        return 0

    if args.cmd == "menu":
        from .menu_bar import start_app
        start_app()
        return 0

    if args.cmd == "auth":
        if args.auth_provider == "antigravity":
            from .auth_oauth import auth_antigravity
            return auth_antigravity(timeout=args.timeout)
        print("用法: usagehub auth antigravity")
        return 2

    if args.cmd == "notify":
        from . import notify as nmod
        if args.test:
            try:
                to = nmod.send_email(cfg, "UsageHub 测试邮件",
                                     "这是一封测试邮件：额度恢复通知的 SMTP 配置正常。")
            except Exception as e:
                print("测试邮件发送失败: {}".format(e))
                return 1
            print("测试邮件已发往 {}".format(to))
            return 0
        try:
            recovered, waste, sent, to = nmod.run(cfg, dry_run=args.dry_run)
        except Exception as e:
            print("检查失败: {}".format(e))
            return 1
        for it in recovered:
            print("✅ {} · {}  已用 {:.0f}% → {:.0f}%（{}）".format(
                it["provider"], it["label"], it["before"], it["after"], it["reason"]))
        for it in waste:
            print("⏳ {} · {}  剩余 {:.0f}%，{}后重置（{}）".format(
                it["provider"], it["label"], it["remaining_pct"],
                it["left_text"], it["reset_local"]))
        if not recovered and not waste:
            print("无需通知：没有「用紧后恢复」的窗口，也没有「快重置却还剩很多」的周/月额度"
                  + ("；dry-run 未更新状态" if args.dry_run else ""))
            return 0
        print("已发邮件至 {}".format(to) if sent else "dry-run：未发邮件、未更新状态")
        return 0

    if args.cmd == "snapshot":
        from .cloud import write_snapshot
        import subprocess
        out_dir = Path(args.out)
        # 一次探测两用：既做快照，也顺带检查额度恢复（省一次探测、少一次钥匙串弹窗）
        probe_results = [r.to_dict() for r in run_probes(build_probes(cfg, None))]
        out_file = write_snapshot(cfg, out_dir, results=probe_results)
        print("已生成加密快照: {}".format(out_file))
        if (cfg.get("notify") or {}).get("enabled", True):
            try:
                from . import notify as nmod
                rec, waste, sent, to = nmod.run(cfg, results=probe_results)
                if rec or waste:
                    print("通知：恢复 {} 项 / 待用完 {} 项，{}".format(
                        len(rec), len(waste),
                        "已发邮件至 {}".format(to) if sent else "邮件未发出"))
            except Exception as e:
                print("恢复通知检查跳过: {}".format(e))  # 不影响快照部署
        if args.deploy:
            print("部署到 Vercel …")
            r = subprocess.run(
                ["npx", "vercel", "deploy", "--prod", "--yes"],
                cwd=str(out_dir), capture_output=True, text=True)
            out = (r.stdout + r.stderr).strip()
            print(out[-500:])
            return r.returncode
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
            used_pct = None if w.remaining_pct is None else 100.0 - w.remaining_pct
            if used_pct is not None:
                parts.append("已用 {:>5.1f}%".format(used_pct))
            if w.remaining_abs is not None:
                if w.limit_abs:
                    used_abs = round(w.limit_abs - w.remaining_abs, 4)
                    rng = "{:g}/{:g}".format(used_abs, w.limit_abs)
                else:
                    rng = "剩 {:g}".format(w.remaining_abs)
                parts.append("${}".format(rng) if w.unit == "$" else
                             "{} {}".format(rng, w.unit).strip())
            if w.resets_at:
                if _is_weekly(w.label):
                    wd = _weekday(w.resets_at)
                    if wd:
                        parts.append("{} 重置".format(wd))
                else:
                    cd = _countdown(w.resets_at, now)
                    if cd:
                        parts.append("重置于 {} 后".format(cd))
            bar = _bar(used_pct)
            print("   {:<24} {} {}".format(w.label, bar, "  ".join(parts)))
        if r.error:  # 部分成功时的警告
            print("   ⚠ {}".format(r.error))


def _bar(pct, width=20):
    if pct is None:
        return " " * (width + 2)
    filled = int(round(pct / 100.0 * width))
    return "[" + "█" * filled + "░" * (width - filled) + "]"


_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _is_weekly(label):
    """周窗口：标签含"周"且不含"月"（区别于每月窗口）。"""
    return "周" in label and "月" not in label


def _weekday(resets_at):
    """把重置时间转成本地时区的"周X HH:MM"。"""
    try:
        t = datetime.fromisoformat(resets_at.replace("Z", "+00:00")).astimezone()
        return "{} {:02d}:{:02d}".format(_WEEKDAYS[t.weekday()], t.hour, t.minute)
    except Exception:
        return None


def _countdown(resets_at, now):
    try:
        t = datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
        delta = t - now
        secs = int(delta.total_seconds())
        if secs <= 0:
            return None
        d, rem = divmod(secs, 86400)
        h, rem = divmod(rem, 3600)
        m = rem // 60
        if d:
            return "{}天{}小时".format(d, h) if h else "{}天".format(d)
        return "{}h{:02d}m".format(h, m) if h else "{}m".format(m)
    except Exception:
        return None


if __name__ == "__main__":
    sys.exit(main())
