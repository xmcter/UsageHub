# -*- coding: utf-8 -*-
"""Antigravity 多账号额度探测。

优先级：
1. 云端 retrieveUserQuota（多账号，无需开 IDE）
   凭据来源：config.accounts / CodexBar tokenAccounts / 钥匙串 gemini·antigravity /
   ~/.codexbar/antigravity/oauth_creds.json
2. 本机 language server GetUserStatus（当前登录实例，自动发现多进程）
"""
import base64
import json
import os
import re
import subprocess
from pathlib import Path

from .base import ProviderProbe
from ..core import Window

RPC_PATH = "/exa.language_server_pb.LanguageServerService/GetUserStatus"
RPC_BODY = {"metadata": {"ideName": "antigravity", "extensionName": "antigravity", "locale": "en"}}
CLOUD_QUOTA_URL = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota"
TOKENINFO_URL = "https://www.googleapis.com/oauth2/v3/tokeninfo"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# CodexBar / Antigravity 桌面端常见 OAuth client（用于 refresh）
DEFAULT_CLIENTS = [
    # CodexBar Antigravity OAuth
    ("884354919052-36trc1jjb3tguiac32ov6cod268c5blh.apps.googleusercontent.com",
     ""),
    # Antigravity / Gemini 桌面端
    ("1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com",
     ""),
]


class AntigravityProbe(ProviderProbe):
    name = "antigravity"
    display_name = "Antigravity"

    def __init__(self, cfg, global_cfg, pid=None, csrf=None, ports=None,
                 account_email="", access_token="", refresh_token="",
                 client_id="", client_secret="", source="local"):
        super().__init__(cfg, global_cfg)
        self.pid = pid
        self.csrf = csrf
        self.ports = ports or []
        self.account_email = account_email or ""
        self.access_token = access_token or ""
        self.refresh_token = refresh_token or ""
        self.client_id = client_id or ""
        self.client_secret = client_secret or ""
        self.source = source

    @classmethod
    def create_probes(cls, cfg, global_cfg):
        probes = []
        seen_emails = set()

        for acc in cls._discover_accounts(cfg):
            email = (acc.get("email") or "").lower()
            if email and email in seen_emails:
                continue
            if email:
                seen_emails.add(email)
            probes.append(cls(
                cfg, global_cfg,
                account_email=acc.get("email") or "",
                access_token=acc.get("access_token") or "",
                refresh_token=acc.get("refresh_token") or "",
                client_id=acc.get("client_id") or "",
                client_secret=acc.get("client_secret") or "",
                source=acc.get("source") or "cloud",
            ))

        # 本地 language server：补当前已开实例（可能是尚未配云端凭据的账号）
        for pid, csrf, ports in cls._find_processes():
            probes.append(cls(cfg, global_cfg, pid=pid, csrf=csrf, ports=ports, source="local"))

        if not probes:
            return [cls(cfg, global_cfg)]
        return probes

    def fetch(self):
        if self.access_token or self.refresh_token:
            return self._fetch_cloud()
        if self.ports:
            return self._fetch_local()
        return self.fail(
            "未找到 Antigravity 账号凭据，也未发现 language server。"
            "可在 CodexBar 登录该 Google 账号，或在 config.providers.antigravity.accounts 填 refresh_token。"
        )

    # ---- 云端 ----
    def _fetch_cloud(self):
        token = self.access_token
        if not token and self.refresh_token:
            token = self._refresh_access_token()
            if not token:
                return self.fail("refresh_token 换 access_token 失败（{}）".format(self.account_email or self.source))
            self.access_token = token

        email = self.account_email or self._token_email(token) or self.source
        s = self.session(use_proxy=True)
        # UA 必须带 antigravity，否则 Google 只返回 Code Assist 的 gemini-2.5 泛化 bucket，
        # 拿不到 Antigravity 真实的 gemini-3 系列额度
        def _headers(tok):
            return {
                "Authorization": "Bearer {}".format(tok),
                "Content-Type": "application/json",
                "User-Agent": "antigravity",
            }
        try:
            resp = s.post(CLOUD_QUOTA_URL, headers=_headers(token), data="{}", timeout=20)
            if resp.status_code in (401, 403) and self.refresh_token:
                token = self._refresh_access_token()
                if token:
                    self.access_token = token
                    resp = s.post(CLOUD_QUOTA_URL, headers=_headers(token), data="{}", timeout=20)
            if resp.status_code != 200:
                return self.fail("云端配额 HTTP {}（{}）: {}".format(
                    resp.status_code, email, resp.text[:160].replace("\n", " ")))
            return self._parse_cloud(resp.json(), email)
        except Exception as e:
            return self.fail("云端配额请求失败（{}）: {}".format(email, type(e).__name__))

    def _parse_cloud(self, data, email):
        buckets = data.get("buckets") if isinstance(data, dict) else None
        if not buckets:
            return self.fail("云端返回无 buckets（{}）".format(email))
        # 同额度同重置时间的模型合并成一个窗口（对齐本地 LS 的分组展示）
        groups = {}
        for b in buckets:
            if not isinstance(b, dict):
                continue
            model = str(b.get("modelId") or b.get("tokenType") or "quota")
            ml = model.lower()
            # PROMPT credits 月额度噪音大，与本地探测一致隐藏；tab_* 是补全内部模型
            if ("prompt" in ml and "credit" in ml) or ml.startswith("tab_"):
                continue
            frac = b.get("remainingFraction")
            # proto3 JSON 省略零值：有 modelId 但没 remainingFraction = 已用尽
            pct = float(frac) * 100.0 if isinstance(frac, (int, float)) else 0.0
            key = (round(pct, 2), b.get("resetTime") or "")
            groups.setdefault(key, []).append(model)
        if not groups:
            return self.fail("云端 buckets 为空（{}）".format(email))
        windows = []
        for (pct, reset), models in sorted(groups.items(), key=lambda kv: kv[0][0]):
            # 共享同一额度池的模型合并成一行，但**列全所有模型名**——
            # 早先超过 4 个就折叠成「max(models) 等 N 个模型」，代表名按字母序随机挑、
            # 其余名字全丢，导致云端账号看着比本地账号少一大截（用户 2026-07-19 反馈）
            label = " / ".join(sorted(models))
            windows.append(Window(
                label=label,
                remaining_pct=pct,
                resets_at=reset or None,
            ))
        return self.result(True, windows=windows, account=email, plan="cloud")

    def _refresh_access_token(self):
        if not self.refresh_token:
            return ""
        clients = []
        if self.client_id:
            clients.append((self.client_id, self.client_secret or ""))
        clients.extend(DEFAULT_CLIENTS)
        s = self.session(use_proxy=True)
        for cid, csec in clients:
            if not cid:
                continue
            data = {
                "client_id": cid,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            }
            if csec:
                data["client_secret"] = csec
            try:
                resp = s.post(TOKEN_URL, data=data, timeout=20)
                if resp.status_code == 200:
                    tok = resp.json().get("access_token") or ""
                    if tok:
                        self.client_id = cid
                        self.client_secret = csec
                        return tok
            except Exception:
                continue
        return ""

    def _token_email(self, token):
        if not token:
            return ""
        try:
            s = self.session(use_proxy=True)
            r = s.get(TOKENINFO_URL, params={"access_token": token}, timeout=12)
            if r.status_code == 200:
                return r.json().get("email") or ""
        except Exception:
            pass
        return ""

    # ---- 本地 LS ----
    def _fetch_local(self):
        s = self.session(use_proxy=False)
        errors = []
        for port in self.ports:
            for scheme in ("https", "http"):
                url = "{}://127.0.0.1:{}{}".format(scheme, port, RPC_PATH)
                try:
                    headers = {
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Connect-Protocol-Version": "1",
                    }
                    if self.csrf:
                        headers["X-Codeium-Csrf-Token"] = self.csrf
                    resp = s.post(url, headers=headers, data=json.dumps(RPC_BODY),
                                  timeout=6, verify=False)
                    if resp.status_code != 200:
                        errors.append("{} -> HTTP {}".format(port, resp.status_code))
                        continue
                    return self._parse_local(resp.json())
                except Exception as e:
                    errors.append("{}({}) {}".format(port, scheme, type(e).__name__))
        return self.fail("language server (PID {}) 在跑但接口未打通: {}".format(
            self.pid, "; ".join(errors[:8])))

    def _parse_local(self, data):
        status = data.get("userStatus") if isinstance(data, dict) else None
        status = status or (data if isinstance(data, dict) else {})
        windows = []
        plan_status = status.get("planStatus") or {}
        cascade = status.get("cascadeModelConfigData") or {}
        for m in cascade.get("clientModelConfigs") or []:
            if not isinstance(m, dict):
                continue
            label = m.get("label") or (m.get("modelOrAlias") or {}).get("model") or "unknown"
            qi = m.get("quotaInfo") or {}
            frac = qi.get("remainingFraction")
            if frac is None and not qi:
                continue
            windows.append(Window(
                label=str(label),
                remaining_pct=float(frac) * 100.0 if isinstance(frac, (int, float)) else None,
                resets_at=qi.get("resetTime"),
            ))
        if not windows:
            return self.fail("GetUserStatus 打通了但没解析到额度字段: {}".format(
                json.dumps(data)[:300]))
        account = status.get("email") or ""
        plan = (plan_status.get("planInfo") or {}).get("name") or "local"
        return self.result(True, windows=windows, account=account, plan=str(plan))

    # ---- 账号发现 ----
    @classmethod
    def _discover_accounts(cls, cfg):
        found = []
        # 1) 用户显式配置
        for a in (cfg.get("accounts") or []):
            if not isinstance(a, dict):
                continue
            if a.get("access_token") or a.get("refresh_token"):
                found.append({
                    "email": a.get("email") or "",
                    "access_token": a.get("access_token") or "",
                    "refresh_token": a.get("refresh_token") or "",
                    "client_id": a.get("client_id") or "",
                    "client_secret": a.get("client_secret") or "",
                    "source": "config",
                })

        # 2) CodexBar tokenAccounts
        try:
            cb = Path.home() / ".codexbar" / "config.json"
            if cb.exists():
                data = json.loads(cb.read_text())
                for p in data.get("providers") or []:
                    if p.get("id") != "antigravity":
                        continue
                    for a in ((p.get("tokenAccounts") or {}).get("accounts") or []):
                        t = a.get("token") or {}
                        if isinstance(t, str):
                            try:
                                t = json.loads(t)
                            except Exception:
                                t = {"access_token": t}
                        if not isinstance(t, dict):
                            continue
                        if t.get("access_token") or t.get("refresh_token"):
                            found.append({
                                "email": a.get("externalIdentifier") or t.get("email") or a.get("label") or "",
                                "access_token": t.get("access_token") or "",
                                "refresh_token": t.get("refresh_token") or "",
                                "client_id": t.get("client_id") or "",
                                "client_secret": t.get("client_secret") or "",
                                "source": "codexbar",
                            })
        except Exception:
            pass

        # 3) CodexBar oauth_creds.json
        try:
            of = Path.home() / ".codexbar" / "antigravity" / "oauth_creds.json"
            if of.exists():
                t = json.loads(of.read_text())
                if t.get("access_token") or t.get("refresh_token"):
                    found.append({
                        "email": t.get("email") or "",
                        "access_token": t.get("access_token") or "",
                        "refresh_token": t.get("refresh_token") or "",
                        "client_id": t.get("client_id") or "",
                        "client_secret": t.get("client_secret") or "",
                        "source": "oauth_file",
                    })
        except Exception:
            pass

        # 4) macOS 钥匙串 gemini / antigravity（当前桌面端登录号）
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
                capture_output=True, text=True, timeout=8,
            ).stdout.strip()
            if out.startswith("go-keyring-base64:"):
                dec = json.loads(base64.b64decode(out.split(":", 1)[1]))
                tok = dec.get("token") or {}
                if tok.get("access_token") or tok.get("refresh_token"):
                    found.append({
                        "email": "",
                        "access_token": tok.get("access_token") or "",
                        "refresh_token": tok.get("refresh_token") or "",
                        "client_id": "",
                        "client_secret": "",
                        "source": "keychain",
                    })
        except Exception:
            pass

        return found

    @staticmethod
    def _find_processes():
        procs = []
        seen_ports = set()

        env_addr = os.environ.get("ANTIGRAVITY_LS_ADDRESS")
        env_csrf = os.environ.get("ANTIGRAVITY_CSRF_TOKEN")
        if env_addr:
            m = re.search(r':(\d+)$', env_addr)
            if m:
                port = int(m.group(1))
                procs.append(("env", env_csrf, [port]))
                seen_ports.add(port)

        try:
            out = subprocess.run(["ps", "-axo", "pid=,command="],
                                 capture_output=True, text=True, timeout=10).stdout
        except Exception:
            out = ""

        for line in out.splitlines():
            if "language_server" not in line or "antigravity" not in line.lower():
                continue
            pid = line.strip().split(None, 1)[0]
            csrf = _arg(line, "--csrf_token")
            ports = []
            for flag in ("--extension_server_port", "--ls_port", "--server_port"):
                v = _arg(line, flag)
                if v and v.isdigit():
                    ports.append(int(v))
            ports += AntigravityProbe._listening_ports(pid)
            uniq = []
            for p in ports:
                if p not in seen_ports:
                    seen_ports.add(p)
                    uniq.append(p)
            if uniq:
                procs.append((pid, csrf, uniq))
        return procs

    @staticmethod
    def _listening_ports(pid):
        try:
            out = subprocess.run(
                ["lsof", "-nP", "-a", "-iTCP", "-sTCP:LISTEN", "-p", str(pid)],
                capture_output=True, text=True, timeout=10).stdout
        except Exception:
            return []
        return [int(m.group(1)) for m in re.finditer(r":(\d+)\s+\(LISTEN\)", out)]


def _arg(cmdline, flag):
    m = re.search(re.escape(flag) + r"[= ]([^\s]+)", cmdline)
    return m.group(1) if m else None
