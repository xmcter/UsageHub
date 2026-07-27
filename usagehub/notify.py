# -*- coding: utf-8 -*-
"""额度邮件通知：① 用紧后恢复了；② 快到期还剩很多（别浪费）。

**① 恢复通知**（`detect`）：某家额度用紧了（比如 Claude 5 小时窗打到 90%），
等它重置恢复后发一封邮件告知，不用自己盯面板。判定要求**之前确实紧张过**
（已用 >= alert_threshold）**且现在用量真的降下来了**——只看 `resets_at` 变化会误报，
因为接口返回的重置时间戳低位会抖动（见 `_norm_reset`）。

**② 浪费提醒**（`detect_waste`）：周/月这类长周期额度，**距重置只剩 N 天（默认 2 天）
却还剩很多没用**（已用 <= waste_threshold）时提醒一次，免得白白过期。
只针对周/月窗口——5 小时窗每天滚好几轮，提醒没意义。
**每个窗口每个周期只提醒一次**（靠状态里的 `waste_alerted_for` 记住已提醒的周期），
否则最后两天里每轮检查都发信会刷屏。

状态存 `~/.usagehub/notify_state.json`（每个窗口的已用%、重置时间、已提醒周期）。
**首次运行只建基线、不发恢复邮件**，避免上线即误报。

SMTP 凭据默认**复用 MacPush 的配置**（`~/Library/Application Support/MacPush/config.json`），
密钥只存一处、不在本项目里复制一份。也可在 `~/.usagehub/config.json` 的 `notify` 段自行填。
"""
import json
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from .core import run_probes
from .providers import build_probes

STATE_PATH = Path.home() / ".usagehub" / "notify_state.json"
MACPUSH_CONFIGS = [
    Path.home() / "Library" / "Application Support" / "MacPush" / "config.json",
    Path.home() / "Library" / "Application Support" / "MacPushToAndroid" / "config.json",
]

DEFAULT_ALERT = 80.0     # 已用% 达到多少算「紧张」（只有紧张过的窗口才在恢复时通知）
DEFAULT_RECOVER = 30.0   # 已用% 回落到多少算「恢复」

# 浪费提醒：距重置 <= N 天、且已用 <= X% 时提醒「还剩很多别浪费」
# 阈值 80 = 只要还剩 20% 以上没用就值得提醒（用户口径："已用低于 80% 就是好多了"）
DEFAULT_WASTE_DAYS = 2.0
DEFAULT_WASTE_THRESHOLD = 80.0


# ---------- SMTP 配置 ----------

def smtp_settings(cfg: dict):
    """返回 (host, port, sender, password, receiver)；缺配置则抛错说明怎么补。"""
    n = (cfg.get("notify") or {}).get("email") or {}
    host = (n.get("smtp_server") or "").strip()
    sender = (n.get("sender") or "").strip()
    password = n.get("password") or ""
    receiver = (n.get("receiver") or "").strip()
    port = int(n.get("smtp_port") or 465)

    # 未自行配置时，复用 MacPush 的 SMTP（密钥只存一处）
    if n.get("inherit_macpush", True) and not (host and sender and password):
        for p in MACPUSH_CONFIGS:
            if not p.exists():
                continue
            try:
                m = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            host = host or (m.get("email_smtp_server") or "").strip()
            port = int(n.get("smtp_port") or m.get("email_smtp_port") or 465)
            sender = sender or (m.get("email_sender") or "").strip()
            password = password or m.get("email_password") or ""
            receiver = receiver or (m.get("email_receiver") or "").strip()
            if host and sender and password:
                break

    receiver = receiver or sender
    if not (host and sender and password and receiver):
        raise RuntimeError(
            "缺少 SMTP 配置。两种补法：\n"
            "  1) 在 MacPush 里配好邮箱（本模块默认复用它，密钥只存一处）；\n"
            "  2) 在 ~/.usagehub/config.json 的 notify.email 填 smtp_server / smtp_port / "
            "sender / password / receiver。"
        )
    return host, port, sender, password, receiver


def send_email(cfg: dict, subject: str, body: str):
    host, port, sender, password, receiver = smtp_settings(cfg)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("UsageHub", sender))
    msg["To"] = receiver
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if int(port) == 465:
        with smtplib.SMTP_SSL(host, int(port), context=ctx, timeout=30) as s:
            s.login(sender, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, int(port), timeout=30) as s:
            s.starttls(context=ctx)
            s.login(sender, password)
            s.send_message(msg)
    return receiver


# ---------- 状态与判定 ----------

def _key(result, window):
    return "{}|{}|{}".format(result.get("provider", ""),
                             result.get("account", ""),
                             window.get("label", ""))


def _norm_reset(iso):
    """把重置时间规范到「分钟」再比较。

    Claude 的接口每次返回的 resets_at 微秒位都在抖动
    （...00.833050 / ...00.816484 是同一个 08:50 重置点），
    直接比字符串会把每次探测都误判成「窗口滚动」。
    """
    s = (iso or "").strip()
    if not s:
        return ""
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
        return t.strftime("%Y-%m-%dT%H:%M")
    except Exception:
        return s[:16]


def load_state():
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        STATE_PATH.chmod(0o600)
    except OSError:
        pass


def detect(results, prev, alert=DEFAULT_ALERT, recover=DEFAULT_RECOVER):
    """比对上次状态，返回 (恢复列表, 新状态)。恢复项含 provider/账号/窗口/前后用量/原因。"""
    recovered = []
    new_state = {}
    for r in results:
        if not r.get("ok"):
            continue
        for w in r.get("windows", []):
            pct = w.get("remaining_pct")
            if pct is None:
                continue
            used = 100.0 - float(pct)
            k = _key(r, w)
            old = prev.get(k)
            cur = {"used": round(used, 2), "resets_at": _norm_reset(w.get("resets_at"))}
            # 关键：把浪费提醒的去重标记继承下来，否则每轮状态覆盖后会重复发信
            if old and old.get("waste_alerted_for"):
                cur["waste_alerted_for"] = old["waste_alerted_for"]
            new_state[k] = cur

            if not old:
                continue  # 首次见到该窗口：只建基线
            old_used = float(old.get("used", 0))
            if old_used < alert:
                continue  # 之前没紧张过，恢复了也不值得打扰
            # 关键：必须「用量确实降下来了」才算恢复。
            # 只看 resets_at 变化会误报——接口返回的重置时间戳会抖动（见 _norm_reset）；
            # 窗口滚动仅作为辅助信号，且仍要求有明显降幅。
            rolled = bool(cur["resets_at"]) and cur["resets_at"] != old.get("resets_at")
            if used <= recover:
                reason = "窗口已重置" if rolled else "用量回落"
            elif rolled and used <= old_used - 30.0:
                reason = "窗口已重置（用量大幅回落）"
            else:
                continue
            recovered.append({
                "provider": r.get("display_name") or r.get("provider"),
                "account": r.get("account") or r.get("plan") or "",
                "label": w.get("label", ""),
                "before": old_used,
                "after": used,
                "reason": reason,
            })
    return recovered, new_state


def _is_long_window(label):
    """是不是周/月这类长周期窗口。

    只有长周期额度才值得做「快到期别浪费」提醒——5 小时窗每天滚好几轮，提醒没意义。
    靠标签里的「周」/「月」判断（与 CLI、状态栏的判断口径一致）：
    命中「周窗口(整体)」「每周窗口」「每月窗口」「每周限额（…）」等；
    「5小时窗口」与 Antigravity 的模型名标签都不命中。
    """
    s = label or ""
    return ("周" in s) or ("月" in s)


def _fmt_remaining(seconds):
    d, rem = divmod(int(seconds), 86400)
    h = rem // 3600
    if d:
        return "{}天{}小时".format(d, h) if h else "{}天".format(d)
    m = (rem % 3600) // 60
    return "{}小时{}分".format(h, m) if h else "{}分钟".format(m)


def detect_waste(results, state, days=DEFAULT_WASTE_DAYS,
                 threshold=DEFAULT_WASTE_THRESHOLD, now=None):
    """找出「快重置了却还剩很多没用」的周/月额度，提醒别浪费。

    条件：长周期窗口 + 距重置 <= days 天 + 已用 <= threshold%。
    **每个窗口每个周期只提醒一次**：`state[key]["waste_alerted_for"]` 记住已提醒过的周期
    （用规范化后的 resets_at 作周期标识），下个周期该值不同才会再次提醒。
    直接在传入的 `state` 上打标记；返回待提醒列表。
    """
    now = now or datetime.now(timezone.utc)
    pending = []
    for r in results:
        if not r.get("ok"):
            continue
        for w in r.get("windows", []):
            label = w.get("label", "")
            pct = w.get("remaining_pct")
            iso = w.get("resets_at")
            if pct is None or not iso or not _is_long_window(label):
                continue
            try:
                reset_dt = datetime.fromisoformat(
                    iso.replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                continue
            left = (reset_dt - now).total_seconds()
            if left <= 0 or left > days * 86400:
                continue          # 已过期，或还没进入提醒窗口
            used = 100.0 - float(pct)
            if used > threshold:
                continue          # 用得够多了，不算浪费
            k = _key(r, w)
            period = _norm_reset(iso)
            entry = state.setdefault(k, {"used": round(used, 2), "resets_at": period})
            if entry.get("waste_alerted_for") == period:
                continue          # 本周期已提醒过，不重复打扰
            entry["waste_alerted_for"] = period
            pending.append({
                "provider": r.get("display_name") or r.get("provider"),
                "account": r.get("account") or r.get("plan") or "",
                "label": label,
                "used": used,
                "remaining_pct": float(pct),
                "left_text": _fmt_remaining(left),
                "reset_local": reset_dt.astimezone().strftime("%m-%d %H:%M"),
            })
    return pending


def compose_waste(pending):
    """把「别浪费」提醒汇总成一封邮件。"""
    n = len(pending)
    first = pending[0]
    if n == 1:
        subject = "⏳ {} {} 还剩 {:.0f}% 未用，{}后重置".format(
            first["provider"], first["label"], first["remaining_pct"], first["left_text"])
    else:
        subject = "⏳ {} 项额度即将重置，还有较多未使用".format(n)
    lines = ["以下额度即将重置，但还剩不少没用完——想用的话趁现在：", ""]
    for it in pending:
        who = "{}{}".format(it["provider"], "（{}）".format(it["account"]) if it["account"] else "")
        lines.append("• {} · {}".format(who, it["label"]))
        lines.append("    剩余 {:.0f}%（已用 {:.0f}%） · {}后重置（{}）".format(
            it["remaining_pct"], it["used"], it["left_text"], it["reset_local"]))
    lines += ["", "（每个周期只提醒一次；阈值可在 ~/.usagehub/config.json 的 notify 段调整）",
              "—— UsageHub · {}".format(
                  datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"))]
    return subject, "\n".join(lines)


def compose(recovered):
    """把恢复项汇总成一封邮件（多条合并，避免刷屏）。"""
    n = len(recovered)
    first = recovered[0]
    if n == 1:
        subject = "✅ {} {} 额度已恢复".format(first["provider"], first["label"])
    else:
        subject = "✅ {} 项 AI 额度已恢复（{} 等）".format(n, first["provider"])
    lines = ["以下额度已恢复可用：", ""]
    for it in recovered:
        who = "{}{}".format(it["provider"], "（{}）".format(it["account"]) if it["account"] else "")
        lines.append("• {} · {}".format(who, it["label"]))
        lines.append("    已用 {:.0f}% → {:.0f}%（{}）".format(it["before"], it["after"], it["reason"]))
    lines += ["", "—— UsageHub · {}".format(
        datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"))]
    return subject, "\n".join(lines)


def run(cfg: dict, dry_run=False, results=None):
    """检查两类通知 → 该发就发邮件。

    返回 (恢复列表, 浪费提醒列表, 是否发过邮件, 收件人)。
    `results` 可传入已有的探测结果（如 snapshot 刚跑完的），避免重复探测。
    两类各发一封（内容性质不同，不合并），互不影响：一类失败不影响另一类。
    """
    if results is None:
        results = [r.to_dict() for r in run_probes(build_probes(cfg, None))]
    n = cfg.get("notify") or {}
    prev = load_state()

    # ① 恢复通知
    recovered, new_state = detect(
        results, prev,
        alert=float(n.get("alert_threshold", DEFAULT_ALERT)),
        recover=float(n.get("recover_threshold", DEFAULT_RECOVER)),
    )
    # ② 浪费提醒（在 new_state 上打去重标记，随后一起落盘）
    pending_waste = []
    if n.get("waste_reminder", True):
        pending_waste = detect_waste(
            results, new_state,
            days=float(n.get("waste_days", DEFAULT_WASTE_DAYS)),
            threshold=float(n.get("waste_threshold", DEFAULT_WASTE_THRESHOLD)),
        )

    sent_to = None
    if not dry_run:
        if recovered:
            subject, body = compose(recovered)
            sent_to = send_email(cfg, subject, body)
        if pending_waste:
            subject, body = compose_waste(pending_waste)
            sent_to = send_email(cfg, subject, body)
        save_state(new_state)   # dry-run 不落盘，免得把待通知的变化吃掉
    return recovered, pending_waste, bool(sent_to), sent_to
