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

CONFIG_PATH = Path.home() / ".usagehub" / "config.json"
# 环形进度模板图标：assets/ring_000..ring_100.png（每 5% 一张，单色 + 透明底）。
# set template=True 后 macOS 自动跟随亮/暗色反白。运行时按「当前最吃紧额度的已用%」
# 选最近一张切换，图标即成实时进度环。menubar_icon.png 是无数据时的兜底。
ICON_DIR = Path(__file__).resolve().parent / "assets"
ICON_PATH = str(ICON_DIR / "menubar_icon.png")

# 点产品表头 → 打开对应 App（优先）或官网（回退）
PROVIDER_APP = {"claude": "Claude", "antigravity": "Antigravity", "commandcode": "Command Code"}
PROVIDER_URL = {
    "claude": "https://claude.ai",
    "grok": "https://grok.com",
    "cline": "https://app.cline.bot",
    "antigravity": "https://antigravity.google",
    "commandcode": "https://commandcode.ai/tonycter7zwi/settings/usage",
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
            requests.get("http://127.0.0.1:{}/".format(self.port), timeout=2)
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
        # 优先打开本机 App，装不了/没有则回退官网
        app = PROVIDER_APP.get(provider)
        if app:
            try:
                if subprocess.run(["open", "-a", app],
                                  capture_output=True).returncode == 0:
                    return
            except Exception:
                pass
        url = PROVIDER_URL.get(provider)
        if url:
            webbrowser.open(url)

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
        self.load_credentials()
        url = f"http://127.0.0.1:{self.port}/api/usage"
        if force:
            url += "?force=1"
            
        try:
            auth = (self.username, self.password) if self.password else None
            resp = requests.get(url, auth=auth, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                self.update_menu_items(data.get("results", []))
            elif resp.status_code == 401:
                self.show_error("认证失败: 请检查 config.json 账密")
            else:
                self.show_error(f"HTTP 错误 {resp.status_code}")
        except Exception as e:
            # 后端掉了（崩溃 / 被手动 kill / 睡眠唤醒后没恢复）：拉起来重试一次，
            # 而不是把「连接失败」摆在那儿等用户自己去开终端
            if self.ensure_backend():
                try:
                    auth = (self.username, self.password) if self.password else None
                    resp = requests.get(url, auth=auth, timeout=15)
                    if resp.status_code == 200:
                        self.update_menu_items(resp.json().get("results", []))
                        return
                except Exception as e2:
                    e = e2
            self.show_error(f"连接失败: {e}")

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

                        # 文字精简：去掉"已用/重置:"前缀，只留百分比与重置点
                        parts = []
                        if used_pct is not None:
                            parts.append(f"{used_pct:.0f}%")
                        iso = w.get("resets_at")
                        if iso:
                            if is_weekly(lbl):
                                wd = weekday_reset(iso)
                                if wd:
                                    parts.append(wd)
                            else:
                                cd = countdown(iso)
                                if cd:
                                    parts.append(f"{cd}后")
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

def start_app():
    UsageHubApp().run()

if __name__ == "__main__":
    start_app()
