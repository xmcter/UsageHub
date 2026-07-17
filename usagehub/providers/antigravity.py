# -*- coding: utf-8 -*-
"""Antigravity：读取本机 language server 的 Connect RPC 接口（无需密钥）。

打法（社区 antigravity-usage / AntigravityQuota 已验证）：
1. 扫描 language_server 进程，从命令行参数提取 --csrf_token 与候选端口；
2. POST https://127.0.0.1:<port>/exa.language_server_pb.LanguageServerService/GetUserStatus
   headers: Connect-Protocol-Version: 1, X-Codeium-Csrf-Token: <token>（自签证书，关闭校验）；
3. 解析 userStatus.cascadeModelConfigData.clientModelConfigs[].quotaInfo
   （remainingFraction 0-1、resetTime，即 5 小时窗口）。
"""
import json
import re
import subprocess

from .base import ProviderProbe
from ..core import Window

RPC_PATH = "/exa.language_server_pb.LanguageServerService/GetUserStatus"
RPC_BODY = {"metadata": {"ideName": "antigravity", "extensionName": "antigravity", "locale": "en"}}


class AntigravityProbe(ProviderProbe):
    name = "antigravity"
    display_name = "Antigravity"

    def fetch(self):
        procs = self._find_processes()
        if not procs:
            return self.fail("未发现 Antigravity language server 进程（Antigravity 没在运行？）")

        s = self.session(use_proxy=False)  # 本地接口，绕过代理
        errors = []
        for pid, csrf, ports in procs:
            for port in ports:
                for scheme in ("https", "http"):
                    url = "{}://127.0.0.1:{}{}".format(scheme, port, RPC_PATH)
                    try:
                        headers = {
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                            "Connect-Protocol-Version": "1",
                        }
                        if csrf:
                            headers["X-Codeium-Csrf-Token"] = csrf
                        resp = s.post(url, headers=headers, data=json.dumps(RPC_BODY),
                                      timeout=6, verify=False)
                        if resp.status_code != 200:
                            errors.append("{} -> HTTP {}".format(port, resp.status_code))
                            continue
                        return self._parse(resp.json())
                    except Exception as e:
                        errors.append("{}({}) {}".format(port, scheme, type(e).__name__))
        return self.fail("language server 在跑但接口未打通: " + "; ".join(errors[:8]))

    # ---- 进程与端口发现 ----
    @staticmethod
    def _find_processes():
        try:
            out = subprocess.run(["ps", "-axo", "pid=,command="],
                                 capture_output=True, text=True, timeout=10).stdout
        except Exception:
            return []
        procs = []
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
            # 去重保序
            seen, uniq = set(), []
            for p in ports:
                if p not in seen:
                    seen.add(p)
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

    # ---- 响应解析 ----
    def _parse(self, data):
        status = data.get("userStatus") if isinstance(data, dict) else None
        status = status or (data if isinstance(data, dict) else {})
        windows = []

        plan_status = status.get("planStatus") or {}
        avail = plan_status.get("availablePromptCredits")
        monthly = (plan_status.get("planInfo") or {}).get("monthlyPromptCredits")
        if isinstance(avail, (int, float)) and isinstance(monthly, (int, float)) and monthly:
            windows.append(Window(
                label="Prompt Credits(月)",
                remaining_pct=avail / monthly * 100.0,
                remaining_abs=avail, limit_abs=monthly, unit="credits",
            ))

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
        plan = (plan_status.get("planInfo") or {}).get("name") or ""
        return self.result(True, windows=windows, account=account, plan=str(plan))


def _arg(cmdline, flag):
    m = re.search(re.escape(flag) + r"[= ]([^\s]+)", cmdline)
    return m.group(1) if m else None
