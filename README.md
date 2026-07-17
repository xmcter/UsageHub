# UsageHub

统一查看多家 AI 订阅的剩余余额/额度（含 5 小时重置窗口倒计时）。跨 Windows / macOS，Android 用手机浏览器看局域网 Web 面板。

支持的订阅（MVP）：

| 服务 | 数据源 | 凭据 |
|---|---|---|
| Claude 订阅（Pro/Max） | 官方 OAuth usage 接口（5 小时窗 + 周窗） | Claude Code OAuth token（自动从钥匙串/`~/.claude/.credentials.json` 找，或 `claude setup-token` 生成后填配置） |
| ClinePass / Cline | `api.cline.bot` 官方 credits 余额 | 自动从 cc-switch 数据库只读提取，或填配置 |
| Antigravity | 本机 language server Connect RPC（按模型 5 小时窗） | 无需配置，进程自动发现（需 Antigravity 在运行） |
| SuperGrok（grok.com） | 网页内部接口 `rest/rate-limits` | 需手动粘贴浏览器 Cookie（未公开接口，会过期） |

## 安装与使用

```bash
pip3 install -r requirements.txt   # 仅依赖 requests

python3 -m usagehub                # 终端表格
python3 -m usagehub --json         # JSON 输出（可接脚本）
python3 -m usagehub --providers claude,antigravity   # 只查部分

python3 -m usagehub serve          # Web 面板 http://127.0.0.1:8787
python3 -m usagehub serve --lan    # 绑 0.0.0.0，安卓手机访问 http://<本机IP>:8787
```

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
