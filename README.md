# UsageHub

一个把多家 AI 订阅的**用量/额度**聚合到一处的小工具：终端、本地 Web 面板、macOS 状态栏三种形态，一眼看清每家还剩多少、几时重置。

> 纯本地、无后端服务、无遥测。所有数据只在你自己的机器上采集与展示。

支持的服务（可各自开关）：

| 服务 | 展示的窗口 | 数据来源 |
|---|---|---|
| **Claude**（Pro/Max 订阅） | 5 小时窗 + 周窗 | 官方 OAuth usage 接口，或浏览器 `claude.ai` 会话 |
| **Cline / ClinePass** | 5 小时 / 周 / 月 | `api.cline.bot` 账户接口 |
| **Grok / SuperGrok** | 每周 SuperGrok 限额（分产品） | `grok.com` 网页内部接口 |
| **Antigravity**（Google） | 各模型 5 小时窗（多账号） | 本机 language server + 云端配额接口 |

## 功能

- **三种形态**：CLI 表格 / `--json`、本地 Web 面板（可局域网访问、可选 Basic Auth）、macOS 状态栏 App。
- **状态栏 App**：菜单里每家显示产品 logo + 彩色进度条（绿/黄/红按用量），点产品名直接打开对应 App/官网；顶部图标是所有额度里最吃紧那条的进度环。
- **统一口径**：一律显示「已用 %」；5 小时窗显示倒计时，周/月窗显示"周几重置 / 还剩几天"。
- **容错**：任一家取数失败不影响其他家；各家并发探测。
- **可选：加密快照上云**：开机时把用量加密后推到任意静态站，手机随时可看；关机时显示最后一次快照（见「加密快照」）。

## 环境要求

> ⚠️ **必须用带 OpenSSL 的 Python（3.11+）**。macOS 自带的 `python3`（3.9）用的是 LibreSSL，
> 经代理连部分现代 HTTPS 站点会报 `SSLEOFError`。用 [uv](https://github.com/astral-sh/uv) 或
> Homebrew 装一个 3.11+ 即可。

## 安装

```bash
git clone https://github.com/xmcter/UsageHub.git
cd UsageHub
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

## 使用

```bash
.venv/bin/python -m usagehub                 # 终端表格
.venv/bin/python -m usagehub --json          # JSON（可接脚本）
.venv/bin/python -m usagehub --providers claude,grok   # 只查部分

.venv/bin/python -m usagehub serve           # 本地 Web 面板 http://127.0.0.1:8787
.venv/bin/python -m usagehub serve --lan     # 绑 0.0.0.0，供局域网/手机浏览器访问
.venv/bin/python -m usagehub menu            # macOS 状态栏 App
```

macOS 想双击启动，可用 `scripts/make-app.sh` 生成一个 `UsageHub.app`（状态栏常驻，自动拉起后端）。

## 配置

首次运行会生成 `~/.usagehub/config.json`（权限 600）。**所有密钥/Cookie 只存在这里，永不进仓库。**

```jsonc
{
  "proxy": "http://127.0.0.1:7897",   // 外网 provider 用的代理，可留空
  "auth_username": "admin",
  "auth_password": "",                 // Web 面板访问密码，留空则不校验
  "providers": {
    "claude":  { "enabled": true, "oauth_token": "" },   // 留空则自动找本机登录态
    "cline":   { "enabled": true, "api_key": "" },
    "grok":    { "enabled": true, "cookie": "" },         // 留空则尝试从浏览器读取
    "antigravity": { "enabled": true }
  }
}
```

各家怎么拿凭据、以及隐私说明见下。

## 数据是怎么来的（务必了解）

这是一个「读你本机已登录状态」的工具，为了拿到各家用量，它会在**本机**读取一些登录凭据：

- **Claude**：优先用 Claude Code 的 OAuth token（钥匙串 / `~/.claude/.credentials.json` / `claude setup-token`）；找不到时可从本机 Chrome 的 `claude.ai` 会话 Cookie 读取（需要你本机钥匙串授权）。
- **Grok**：用你本机浏览器登录 `grok.com` 的 Cookie 调其**网页内部接口**（非公开 API，可能随时变动或失效）。
- **Cline**：从本机 `api.cline.bot` 的 API key 读余额/用量。
- **Antigravity**：读本机正在运行的 language server，或用本机保存的 Google OAuth 凭据调云端配额接口。

**这些都在本地完成，数据不出本机**（除非你显式启用「加密快照上云」）。读取浏览器 Cookie / 钥匙串会触发系统授权弹窗，属正常。若你不接受某家的取数方式，把它 `enabled: false` 即可。

## 加密快照上云（可选）

如果希望**电脑关机时手机也能看**，可以让本机开机时把用量**用访问密码加密**后推到任意静态站：

```bash
.venv/bin/python -m usagehub snapshot --deploy   # 生成加密静态页并部署
```

公网页面只含**密文**，手机端输入密码在浏览器本地解密渲染，**明文永不上云**。配合 `scripts/push-snapshot.sh` + 一个定时器即可自动刷新。

## 架构

```
usagehub/
├── core.py         # Window / ProbeResult 数据模型 + 并发调度
├── config.py       # ~/.usagehub/config.json
├── cli.py          # 终端表格 / --json / 子命令
├── server.py       # stdlib http.server：面板页 + /api/usage（带缓存 + 可选 Basic Auth）
├── cloud.py        # 加密快照生成
├── menu_bar.py     # macOS 状态栏 App（rumps）
├── web/index.html  # 单文件前端（本地实时 + 云端快照双模式）
├── assets/         # 状态栏图标（进度环/条）与产品 logo
└── providers/
    ├── base.py     # ProviderProbe 基类（新增一家 = 继承它 + 注册）
    └── claude.py / cline.py / grok.py / antigravity.py
```

新增一家订阅：继承 `providers/base.py` 的 `ProviderProbe`，实现 `fetch()` 返回若干 `Window`，注册到 `providers/__init__.py`。

## 已知限制

- **Grok / Cline 用的是网页内部接口**，非官方公开 API，可能随平台变动而失效。
- **Antigravity** 的本地实时数据需要其 IDE 正在运行；云端接口是未公开的 gRPC-Web，字段变动会失效。
- 目前 UI 为中文；状态栏 App 仅 macOS。

## 免责声明

本项目仅用于**查看你自己账号的用量**。它读取的是你本机已有的登录状态，不做任何抓取、代理或分享。各服务的商标/logo 归各自所有者，此处仅作产品识别之用。请自行确认你对相关接口的使用符合各服务的条款。

## License

见 `LICENSE`。
