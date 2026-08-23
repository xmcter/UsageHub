# -*- coding: utf-8 -*-
import json
import os
import sys
import subprocess
import time
import requests
import webbrowser
import rumps
from pathlib import Path

from .config import load_config, save_config

try:
    from PyObjCTools import AppHelper
    def _run_on_main_thread(fn, *args, **kwargs):
        AppHelper.callAfter(fn, *args, **kwargs)
except Exception:
    def _run_on_main_thread(fn, *args, **kwargs):
        fn(*args, **kwargs)

CONFIG_PATH = Path.home() / ".usagehub" / "config.json"

# 本地服务请求：绕过系统/环境代理（http_proxy 可能指向已关掉的 Clash 端口，
# 状态栏访问本机 serve 不应走代理）
_LOCAL_SESSION = requests.Session()
_LOCAL_SESSION.trust_env = False
_LOCAL_SESSION.proxies = {"http": None, "https": None}
# 环形进度模板图标：assets/ring_000..ring_100.png（每 5% 一张，单色 + 透明底）。
# set template=True 后 macOS 自动跟随亮/暗色反白。运行时按「当前最吃紧额度的已用%」
# 选最近一张切换，图标即成实时进度环。menubar_icon.png 是无数据时的兜底。
ICON_DIR = Path(__file__).resolve().parent / "assets"
ICON_PATH = str(ICON_DIR / "menubar_icon.png")

# 点产品表头 → 打开对应 agent 客户端（GUI App 全屏 / CLI 终端全屏），没有客户端的回退官网。
# CLI 用绝对路径，不依赖 PATH（状态栏 App 由 launchd 拉起时 PATH 很干净）。
_CLI = {
    "claude": os.path.expanduser("~/.local/bin/claude"),
    "grok": os.path.expanduser("~/.grok/bin/grok"),
}
_APP = {"claude": "Claude", "antigravity": "Antigravity", "commandcode": "Command Code"}
PROVIDER_URL = {
    "claude": "https://claude.ai",
    "grok": "https://grok.com",
    "cline": "https://app.cline.bot",
    "antigravity": "https://antigravity.google",
    "commandcode": "https://commandcode.ai/tonycter7zwi/settings/usage",
    "monocloud": "https://mymonocloud.com",
}

# 状态栏「设置」里每家显示的友好名（与 config.providers 的 key 一一对应）
PROVIDER_LABELS = {
    "claude": "Claude",
    "cline": "ClinePass",
    "grok": "SuperGrok",
    "antigravity": "Antigravity",
    "commandcode": "Command Code",
}

def make_bar(pct, width=10):
    if pct is None:
        return ""
    filled = int(round(pct / 100.0 * width))
    return "[" + "█" * filled + "░" * (width - filled) + "]"

class UsageHubApp(rumps.App):
    def __init__(self):
        if os.path.exists(ICON_PATH):
            super(UsageHubApp, self).__init__("UsageHub", icon=ICON_PATH, template=True)
        else:
            super(UsageHubApp, self).__init__("📊")  # 图标缺失时兜底用 emoji
        self.port = 8787
        self.username = "admin"
        self.password = ""
        self.serve_proc = None          # 由本 App 拉起的 serve 子进程（外部已在跑则为 None）
        self.load_credentials()

        # Build initial static menu structure
        self.update_menu_items([])

        # 状态栏挂着 = 后端就该活着：serve 没在跑就自己拉起来，
        # 用户不必再单独开一个终端敲命令。退出状态栏时会一并收掉（见 on_exit）。
        self.ensure_backend()

        # Start a timer to refresh every 120 seconds
        self.refresh_timer = rumps.Timer(self.on_tick, 120)
        self.refresh_timer.start()

        # Run first load
        self.refresh_data(force=False)

    def backend_alive(self):
        try:
            _LOCAL_SESSION.get("http://127.0.0.1:{}/".format(self.port), timeout=2)
            return True
        except Exception:
            return False

    def ensure_backend(self):
        """serve 没在跑就拉起一个。已经在跑（比如用户自己开的）就不重复起。"""
        if self.backend_alive():
            return True
        try:
            self.serve_proc = subprocess.Popen(
                [sys.executable, "-m", "usagehub", "serve", "--lan"],
                cwd=str(Path(__file__).resolve().parent.parent),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            rumps.notification("UsageHub", "后端启动失败", str(e))
            return False
        # http.server 起得快，但仍给它几秒；轮询比死等 sleep 更快返回
        for _ in range(20):
            time.sleep(0.25)
            if self.backend_alive():
                return True
        return False

    def load_credentials(self):
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    self.username = cfg.get("auth_username", "admin")
                    self.password = cfg.get("auth_password", "")
            except Exception:
                pass

    def on_tick(self, sender):
        self.refresh_data(force=False)

    @rumps.clicked("刷新")
    def on_force_refresh(self, sender):
        self.refresh_data(force=True)

    @rumps.clicked("打开网页面板")
    def on_open_web(self, sender):
        webbrowser.open(f"http://127.0.0.1:{self.port}/")

    def _add_settings_menu(self):
        """构建「设置」子菜单：每家一个勾选项，控制显示/隐藏（写 config.providers.<name>.enabled）。"""
        settings = rumps.MenuItem("设置")
        pcfg = load_config().get("providers", {})
        for name in PROVIDER_LABELS:
            item = rumps.MenuItem(
                PROVIDER_LABELS[name], callback=self._make_toggle_cb(name))
            item.state = 1 if pcfg.get(name, {}).get("enabled", True) else 0
            settings.add(item)
        self.menu.add(settings)

    def _make_toggle_cb(self, provider):
        def cb(sender):
            self.toggle_provider(provider, sender)
        return cb

    def toggle_provider(self, provider, sender):
        """勾/取消勾选某家订阅：改 config 后强制刷新，立即生效。"""
        try:
            cfg = load_config()
            prov = cfg.setdefault("providers", {}).setdefault(provider, {})
            new_state = not prov.get("enabled", True)
            prov["enabled"] = new_state
            save_config(cfg)
            sender.state = 1 if new_state else 0
        except Exception as e:
            self.show_error(f"设置保存失败: {e}")
            return
        self.refresh_data(force=True)

    def _open_provider_cb(self, provider):
        """生成一个「点该产品表头 → 打开对应 App / 官网」的回调。"""
        def cb(sender):
            self.open_provider(provider)
        return cb

    def open_provider(self, provider):
        # 1. CLI 客户端：Terminal 全屏打开并启动（Claude Code / Grok 等）
        cli = _CLI.get(provider)
        if cli and os.path.exists(cli):
            if self._open_cli_fullscreen(cli):
                return
        # 2. GUI App：打开并全屏（Claude / Antigravity / Command Code）
        app = _APP.get(provider)
        if app:
            try:
                if subprocess.run(["open", "-a", app],
                                  capture_output=True).returncode == 0:
                    self._fullscreen_app(app)
                    return
            except Exception:
                pass
        # 3. 回退：官网
        url = PROVIDER_URL.get(provider)
        if url:
            webbrowser.open(url)

    @staticmethod
    def _open_cli_fullscreen(cli_path):
        """Terminal 新窗口打开 CLI，并最大化窗口（zoomed，无需辅助功能权限）。"""
        # 命令要带引号（路径含空格时安全）
        cmd = "{}; exit".format(cli_path)
        # AppleScript 字符串里嵌路径：路径含 \ 或 " 时需转义
        esc_cmd = cmd.replace("\\", "\\\\").replace('"', '\\"')
        script = (
            'tell application "Terminal"\n'
            '    activate\n'
            '    do script "{esc_cmd}"\n'
            '    set zoomed of front window to true\n'
            'end tell\n'
        ).format(esc_cmd=esc_cmd)
        try:
            r = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=15)
            return r.returncode == 0
        except Exception:
            return False

    @staticmethod
    def _fullscreen_app(app_name):
        """让已激活的 App 全屏（Ctrl+Cmd+F；需要辅助功能权限，失败不影响打开）。"""
        try:
            subprocess.run(
                ["osascript", "-e",
                 'tell application "{}" to activate'.format(app_name),
                 "-e",
                 'tell application "System Events" to key code 3 using {control down, command down}'],
                capture_output=True, timeout=15)
        except Exception:
            pass

    @rumps.clicked("退出")
    def on_exit(self, sender):
        # 只收自己拉起的那个 serve；用户自己开的进程不动它
        if self.serve_proc and self.serve_proc.poll() is None:
            try:
                self.serve_proc.terminate()
                self.serve_proc.wait(timeout=5)
            except Exception:
                try:
                    self.serve_proc.kill()
                except Exception:
                    pass
        rumps.quit_application()

    def refresh_data(self, force=False):
        import threading
        threading.Thread(target=self._async_refresh, args=(force,), daemon=True).start()

    def _async_refresh(self, force=False):
        self.load_credentials()
        url = f"http://127.0.0.1:{self.port}/api/usage"
        if force:
            url += "?force=1"
            
        try:
            auth = (self.username, self.password) if self.password else None
            resp = _LOCAL_SESSION.get(url, auth=auth, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                _run_on_main_thread(self.update_menu_items, data.get("results", []))
            elif resp.status_code == 401:
                _run_on_main_thread(self.show_error, "认证失败: 请检查 config.json 账密")
            else:
                _run_on_main_thread(self.show_error, f"HTTP 错误 {resp.status_code}")
        except Exception as e:
            # 后端掉了（崩溃 / 被手动 kill / 睡眠唤醒后没恢复）：拉起来重试一次，
            # 而不是把「连接失败」摆在那儿等用户自己去开终端
            if self.ensure_backend():
                try:
                    auth = (self.username, self.password) if self.password else None
                    resp = _LOCAL_SESSION.get(url, auth=auth, timeout=30)
                    if resp.status_code == 200:
                        _run_on_main_thread(self.update_menu_items, resp.json().get("results", []))
                        return
                except Exception as e2:
                    e = e2
            _run_on_main_thread(self.show_error, f"连接失败: {e}")

    def show_error(self, err_msg):
        self.menu.clear()
        self.menu.add(rumps.MenuItem(f"⚠️ {err_msg}"))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("刷新", callback=self.on_force_refresh))
        self.menu.add(rumps.MenuItem("打开网页面板", callback=self.on_open_web))
        self._add_settings_menu()
        self.menu.add(rumps.MenuItem("退出", callback=self.on_exit))

    def update_menu_items(self, results):
        self.menu.clear()
        
        if not results:
            self.menu.add(rumps.MenuItem("正在加载 AI 配额..."))
        else:
            for r in results:
                account = r.get("account") or r.get("plan") or ""
                name = r.get("display_name") or r.get("provider") or ""
                header_text = f"{name}   {account}" if account else name
                if not r.get("ok"):
                    header_text = "⚠️  " + header_text

                # 点表头 = 打开该产品的 App / 官网（快捷方式）
                header_item = rumps.MenuItem(
                    header_text, callback=self._open_provider_cb(r.get("provider")))
                # 表头左侧放该产品的彩色 logo（不用 template，保留原色便于一眼识别）
                logo = _logo_path(r.get("provider"))
                if logo:
                    header_item.set_icon(logo, dimensions=(20, 20))
                self.menu.add(header_item)
                
                if not r.get("ok"):
                    err_lines = (r.get("error") or "未知错误").splitlines()
                    for el in err_lines[:2]:
                        err_item = rumps.MenuItem(f"    ⚠️ {el[:40]}...")
                        err_item.set_callback(None)
                        self.menu.add(err_item)
                else:
                    for w in r.get("windows", []):
                        lbl = w.get("label", "")
                        pct = w.get("remaining_pct")
                        used_pct = None if pct is None else 100.0 - pct

                        parts = []
                        if used_pct is not None:
                            parts.append(f"{used_pct:.0f}%")

                        limit = w.get("limit_abs")
                        rem = w.get("remaining_abs")
                        unit = w.get("unit", "")
                        period_start = w.get("period_start")

                        if rem is not None and unit and unit != "已启用":
                            if unit == "$":
                                parts.append(f"剩${rem:.2f}")
                            elif unit == "GB":
                                parts.append(f"剩{rem:.1f}GB")
                            elif isinstance(rem, float) and rem.is_integer():
                                parts.append(f"剩{int(rem)}{unit}")
                            else:
                                parts.append(f"剩{rem}{unit}")

                        iso = w.get("resets_at")
                        days_left = None
                        if iso:
                            if is_weekly(lbl):
                                wd = weekday_reset(iso)
                                if wd:
                                    parts.append(wd)
                                days_left = _days_until(iso)
                            else:
                                cd = countdown(iso)
                                if cd:
                                    parts.append(f"{cd}后")
                                days_left = _days_until(iso)

                        # 有总额度且还没到重置点时，展示「剩余日均」与「已用日均」对比，
                        # 判断目前是偏快（会超）还是偏慢（富余）
                        if limit and rem is not None and days_left and days_left > 0:
                            daily_rem = rem / days_left
                            rem_text = _daily_text(daily_rem, unit, "剩")
                            parts.append(rem_text)
                            # 已用日均 = 已用量 / 已过天数（周期起点已知时）
                            elapsed = _days_since(period_start)
                            if elapsed and elapsed > 0:
                                used = limit - rem
                                if used >= 0:
                                    daily_used = used / elapsed
                                    used_text = _daily_text(daily_used, unit, "用")
                                    if daily_used > daily_rem:
                                        parts.append(f"{used_text}↑偏快")
                                    else:
                                        parts.append(f"{used_text}·偏慢")
                        tail = "  ·  ".join(parts)
                        short = _menu_label(lbl)
                        item_text = f"{short}   {tail}" if tail else short

                        win_item = rumps.MenuItem(item_text)
                        # 每行左侧一根横向进度条（按用量绿/黄/红变色，彩色故不用 template）
                        bar = _bar_path(used_pct)
                        if bar:
                            win_item.set_icon(bar, dimensions=(40, 11))
                        win_item.set_callback(None)
                        self.menu.add(win_item)
                        
                self.menu.add(rumps.separator)
                
        # Add actions
        self.menu.add(rumps.MenuItem("刷新", callback=self.on_force_refresh))
        self.menu.add(rumps.MenuItem("打开网页面板", callback=self.on_open_web))
        self._add_settings_menu()
        self.menu.add(rumps.MenuItem("退出", callback=self.on_exit))

        # 状态栏图标 = 实时进度环（跟随最吃紧的那条额度）
        self._apply_progress_icon(results)

    def _apply_progress_icon(self, results):
        """状态栏图标 = 全部窗口「已用%」的平均值（整体用量，而非只盯最吃紧的一条）。"""
        pcts = []
        for r in results or []:
            if not r.get("ok"):
                continue
            for w in r.get("windows", []):
                p = w.get("remaining_pct")
                if p is not None:
                    pcts.append(100.0 - p)
        if not pcts:
            return
        used = max(0.0, min(100.0, sum(pcts) / len(pcts)))
        step = int(round(used / 5.0) * 5)          # 就近取 5% 的档
        path = ICON_DIR / "ring_{:03d}.png".format(step)
        if path.exists():
            self.template = True
            self.icon = str(path)

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _short(label, n=30):
    """截断过长标签（如 Antigravity 整串模型名），菜单里不至于拉得太宽。"""
    label = (label or "").strip()
    return label if len(label) <= n else label[:n - 1].rstrip() + "…"


def _menu_label(label):
    """菜单里的极简窗口名：5小时 / 本周 / 本月；其余截断。"""
    s = (label or "").strip()
    if "小时" in s:
        return "5小时"
    if "月" in s:
        return "本月"
    if "周" in s:
        return "本周"
    return _short(s, 14)


def _ring_path(used_pct):
    """按已用%就近取一枚环形图标（每 5% 一档），用于状态栏顶部图标。"""
    if used_pct is None:
        return None
    step = int(round(max(0.0, min(100.0, used_pct)) / 5.0) * 5)
    p = ICON_DIR / "ring_{:03d}.png".format(step)
    return str(p) if p.exists() else None


def _bar_path(used_pct):
    """按已用%就近取一根横向进度条（每 5% 一档），给菜单行做图形进度。"""
    if used_pct is None:
        return None
    step = int(round(max(0.0, min(100.0, used_pct)) / 5.0) * 5)
    p = ICON_DIR / "bar_{:03d}.png".format(step)
    return str(p) if p.exists() else None


def _logo_path(provider):
    """产品 logo（彩色），给菜单表头行识别是哪家。"""
    if not provider:
        return None
    p = ICON_DIR / "logos" / "{}.png".format(provider)
    return str(p) if p.exists() else None


def is_weekly(label):
    return "周" in (label or "") and "月" not in (label or "")


def weekday_reset(iso):
    """本地时区的"周X HH:MM"。"""
    try:
        from datetime import datetime
        t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        return f"{_WEEKDAYS[t.weekday()]} {t.hour:02d}:{t.minute:02d}"
    except Exception:
        return ""


def countdown(iso):
    if not iso:
        return ""
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        delta = t - datetime.now(timezone.utc)
        secs = int(delta.total_seconds())
        if secs <= 0:
            return ""
        d, rem = divmod(secs, 86400)
        h, rem = divmod(rem, 3600)
        m = rem // 60
        if d:
            return f"{d}天{h}小时" if h else f"{d}天"
        return f"{h}h {m}m" if h else f"{m}m"
    except Exception:
        return ""


def _days_until(iso):
    """距重置还剩多少天（不满 24 小时按 1 天算），解析失败或已过期返回 None。"""
    if not iso:
        return None
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        secs = (t - datetime.now(timezone.utc)).total_seconds()
        if secs <= 0:
            return None
        return max(1, int((secs + 86399) // 86400))
    except Exception:
        return None


def _days_since(iso):
    """周期起点至今过了多少天（不满 24 小时按 1 天算），解析失败返回 None。"""
    if not iso:
        return None
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        secs = (datetime.now(timezone.utc) - t).total_seconds()
        if secs < 0:
            return None
        return max(1, int((secs + 86399) // 86400))
    except Exception:
        return None


def _daily_text(amount, unit, prefix):
    """把日均数值格式化成 "剩$6.00/天" / "用$6.00/天" / "剩5.0 GB/天" 之类。"""
    if unit == "$":
        return "{}{:.2f}/天".format(prefix, amount)
    if unit:
        return "{}{:.1f} {}/天".format(prefix, amount, unit)
    return "{}{:.1f}/天".format(prefix, amount)

def start_app():
    UsageHubApp().run()

if __name__ == "__main__":
    start_app()
