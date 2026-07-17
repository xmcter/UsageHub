# -*- coding: utf-8 -*-
"""配置管理：~/.usagehub/config.json（首次运行自动生成，权限 600）。

密钥/cookie 只存这里，永不进 git。
"""
import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".usagehub"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    # 走外网的 provider（claude/cline/grok）用的代理，如 "http://127.0.0.1:7897"；留空 = 直连
    "proxy": "",
    # Web 面板 /api/usage 的结果缓存秒数（面板轮询不会每次都打上游）
    "cache_seconds": 300,
    "providers": {
        "claude": {
            "enabled": True,
            # 留空则依次尝试：环境变量 CLAUDE_CODE_OAUTH_TOKEN → macOS 钥匙串 → ~/.claude/.credentials.json
            "oauth_token": "",
        },
        "cline": {
            "enabled": True,
            # 留空则从 cc-switch 的数据库里读 ClinePass 的 key（只读）
            "api_key": "",
            "base_url": "https://api.cline.bot",
        },
        "grok": {
            "enabled": True,
            # 必填：grok.com 登录后浏览器里的 Cookie 请求头整串（DevTools → Network → 任一 rest 请求 → Cookie）
            "cookie": "",
            # 要查询的模型与请求类型
            "models": ["grok-4", "grok-3"],
            "request_kinds": ["DEFAULT"],
        },
        "antigravity": {
            "enabled": True,
            # 本地探测，无需配置；language server 端口/csrf 自动发现
        },
    },
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # 合并新增的默认键，老配置升级不丢字段
    merged = _deep_merge(DEFAULT_CONFIG, cfg)
    return merged


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(mode=0o700, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(CONFIG_PATH, 0o600)


def _deep_merge(default: dict, override: dict) -> dict:
    out = {}
    for k, v in default.items():
        if k in override and isinstance(v, dict) and isinstance(override[k], dict):
            out[k] = _deep_merge(v, override[k])
        elif k in override:
            out[k] = override[k]
        else:
            out[k] = v
    for k, v in override.items():
        if k not in out:
            out[k] = v
    return out
