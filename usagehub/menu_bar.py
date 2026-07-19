# -*- coding: utf-8 -*-
import json
import os
import sys
import subprocess
import requests
import webbrowser
import rumps
from pathlib import Path

CONFIG_PATH = Path.home() / ".usagehub" / "config.json"
AUTO_CONFIG_SCRIPT = Path(__file__).parent.parent / "Agent-Mine" / "core" / "rules"
# We'll locate the auto_configure_grok.py script in the brain artifacts directory
SCRATCH_DIR = Path.home() / ".gemini" / "antigravity" / "brain"
# Find any conversational folder that has scratch/auto_configure_grok.py
# Or we can just read the active session script we created:
AUTO_CONFIGURE_PATH = Path("/Users/a123/.gemini/antigravity/brain/78d4b91e-8550-4f69-82d4-72596662d25c/scratch/auto_configure_grok.py")

def make_bar(pct, width=10):
    if pct is None:
        return ""
    filled = int(round(pct / 100.0 * width))
    return "[" + "█" * filled + "░" * (width - filled) + "]"

class UsageHubApp(rumps.App):
    def __init__(self):
        super(UsageHubApp, self).__init__("📊")
        self.title = "📊"
        self.port = 8787
        self.username = "admin"
        self.password = ""
        self.load_credentials()
        
        # Build initial static menu structure
        self.update_menu_items([])
        
        # Start a timer to refresh every 120 seconds
        self.refresh_timer = rumps.Timer(self.on_tick, 120)
        self.refresh_timer.start()
        
        # Run first load
        self.refresh_data(force=False)

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

    @rumps.clicked("强制刷新")
    def on_force_refresh(self, sender):
        self.refresh_data(force=True)

    @rumps.clicked("同步浏览器 Cookie (免密)")
    def on_sync_cookies(self, sender):
        # Run the auto_configure_grok.py script in the background
        if AUTO_CONFIGURE_PATH.exists():
            try:
                # Execute the script
                subprocess.run([sys.executable, str(AUTO_CONFIGURE_PATH)], check=True)
                rumps.notification("UsageHub", "同步成功", "已自动从 Chrome 解密并更新 SuperGrok Cookie！")
                self.refresh_data(force=True)
            except Exception as e:
                rumps.notification("UsageHub", "同步失败", f"错误: {e}")
        else:
            rumps.notification("UsageHub", "同步失败", "未找到 Cookie 同步脚本")

    @rumps.clicked("打开网页面板")
    def on_open_web(self, sender):
        webbrowser.open(f"http://127.0.0.1:{self.port}/")

    @rumps.clicked("退出")
    def on_exit(self, sender):
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
            self.show_error(f"连接失败: {e}")

    def show_error(self, err_msg):
        self.menu.clear()
        self.menu.add(rumps.MenuItem(f"⚠️ {err_msg}"))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("强制刷新", callback=self.on_force_refresh))
        self.menu.add(rumps.MenuItem("同步浏览器 Cookie (免密)", callback=self.on_sync_cookies))
        self.menu.add(rumps.MenuItem("打开网页面板", callback=self.on_open_web))
        self.menu.add(rumps.MenuItem("退出", callback=self.on_exit))

    def update_menu_items(self, results):
        self.menu.clear()
        
        if not results:
            self.menu.add(rumps.MenuItem("正在加载 AI 配额..."))
        else:
            for r in results:
                mark = "🟢" if r.get("ok") else "🔴"
                account = r.get("account") or r.get("plan") or ("正常" if r.get("ok") else "未知状态")
                header_text = f"{mark} {r.get('display_name')} ({account})"
                
                header_item = rumps.MenuItem(header_text)
                header_item.set_callback(None) # disabled label
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
                        rem = w.get("remaining_abs")
                        limit = w.get("limit_abs")
                        unit = w.get("unit", "")
                        
                        used_pct = None if pct is None else 100.0 - pct
                        stats_parts = []
                        if used_pct is not None:
                            stats_parts.append(f"已用 {used_pct:.1f}%")
                        if rem is not None:
                            if limit:
                                used = round(limit - rem, 4)
                                val_str = f"${used:g}/${limit:g}" if unit == "$" else f"{used:g}/{limit:g} {unit}".strip()
                            else:
                                val_str = f"剩 ${rem:g}" if unit == "$" else f"剩 {rem:g} {unit}".strip()
                            stats_parts.append(val_str)
                            
                        # 周窗口直接显示"周X 重置"，其他窗口显示倒计时
                        iso = w.get("resets_at")
                        if iso:
                            if is_weekly(lbl):
                                wd = weekday_reset(iso)
                                if wd:
                                    stats_parts.append(f"{wd} 重置")
                            else:
                                cd = countdown(iso)
                                if cd:
                                    stats_parts.append(f"重置: {cd}")
                            
                        stats_str = " · ".join(stats_parts) if stats_parts else "已启用"
                        
                        # Add visual progress bar inside the menubar menu item!
                        bar_str = make_bar(used_pct)
                        if bar_str:
                            item_text = f"    {lbl:<18}  {bar_str}  {stats_str}"
                        else:
                            item_text = f"    {lbl:<18}  {stats_str}"
                            
                        win_item = rumps.MenuItem(item_text)
                        win_item.set_callback(None)
                        self.menu.add(win_item)
                        
                self.menu.add(rumps.separator)
                
        # Add actions
        self.menu.add(rumps.MenuItem("强制刷新", callback=self.on_force_refresh))
        self.menu.add(rumps.MenuItem("同步浏览器 Cookie (免密)", callback=self.on_sync_cookies))
        self.menu.add(rumps.MenuItem("打开网页面板", callback=self.on_open_web))
        self.menu.add(rumps.MenuItem("退出", callback=self.on_exit))

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


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
