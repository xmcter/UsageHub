# UsageHub

统一查看多家 AI 订阅的剩余余额/额度（含 5 小时重置窗口倒计时）。跨 Windows / macOS，Android 用手机浏览器看局域网 Web 面板。

支持的订阅（MVP）：

| 服务 | 数据源 | 凭据 |
|---|---|---|
| Claude 订阅（Pro/Max） | 官方 OAuth usage 接口，或 `claude.ai/api/organizations/<org>/usage`（5 小时窗 + 周窗） | Claude Code OAuth token（钥匙串/`~/.claude/.credentials.json`/`claude setup-token`），找不到则自动解密 Chrome 的 claude.ai sessionKey |
| ClinePass / Cline | `api.cline.bot` 官方 credits 余额 | 自动从 cc-switch 数据库只读提取，或填配置 |
| Antigravity | 本机 language server + 云端 `retrieveUserQuota`（多账号） | 本地进程自动发现；CodexBar / 钥匙串 / `config.accounts` |
| SuperGrok（grok.com） | 网页内部 gRPC-Web 周限额 | 需手动粘贴浏览器 Cookie（未公开接口，会过期） |

## 安装与使用

> ⚠️ macOS 系统自带 python3 (3.9) 用的是 LibreSSL 2.8.3，经代理连部分 HTTPS 站点会
> `SSLEOFError`。必须用带 OpenSSL 的 Python（3.11+，uv 安装的即可）建 venv 运行。

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt

.venv/bin/python -m usagehub                # 终端表格
.venv/bin/python -m usagehub --json         # JSON 输出（可接脚本）
.venv/bin/python -m usagehub --providers claude,antigravity   # 只查部分

.venv/bin/python -m usagehub serve          # Web 面板 http://127.0.0.1:8787
.venv/bin/python -m usagehub serve --lan    # 绑 0.0.0.0，局域网/隧道反代用
.venv/bin/python -m usagehub menu           # macOS 状态栏（可选）
.venv/bin/python -m usagehub auth antigravity   # 浏览器加 Antigravity 账号（不依赖 CodexBar）
```

**不装开机自启。** 需要看面板时再开 `serve`；公网见 `scripts/FREEDOMAIN.md`（临时 trycloudflare 或固定命名隧道，均为手动）。


首次运行自动生成配置 `~/.usagehub/config.json`（权限 600，密钥/cookie 只存这里，永不进 git）：

- `proxy`：外网 provider 用的代理，如 `http://127.0.0.1:7897`
- `providers.claude.oauth_token`：`claude setup-token` 生成的长期 token（本机有 Claude Code 登录态则免填）
- `providers.grok.cookie`：grok.com 登录后 DevTools 里复制的整串 Cookie 请求头
- `providers.cline.api_key`：留空自动读 cc-switch

## 架构

```
usagehub/
├── core.py            # Window / ProbeResult 数据模型 + 并发调度（一家挂不影响其他家）
├── config.py          # ~/.usagehub/config.json
├── cli.py             # 终端表格 / --json / serve 子命令
├── server.py          # stdlib http.server：/ 面板页 + /api/usage（带缓存）
├── web/index.html     # 单文件前端，卡片 + 倒计时，60s 轮询
└── providers/
    ├── base.py        # ProviderProbe 基类（新增订阅=继承它+注册到 __init__.py）
    ├── claude.py / cline.py / grok.py / antigravity.py
```

## 已知限制

- SuperGrok 走的是 grok.com 未公开接口，cookie 过期/被 Cloudflare 风控时会失败，重新粘贴 cookie 即可。
- Antigravity 需要 IDE 正在运行（数据来自其本地 language server）。
- Claude 的 token 若来自 Claude 桌面版（Electron 加密存储）无法直接读取，需 `claude setup-token` 生成一次长期 token。
