#!/usr/bin/env bash
# UsageHub Cloudflare 命名隧道：手动按需启动（不装开机自启 / launchd）
#
# 已就绪状态（2026-07-18 实测可用）：
#   - DigitalPlat 注册主域名: forcine.dpdns.org（免费 permanent）
#   - DigitalPlat 侧 NS 已改为 Cloudflare: donovan.ns.cloudflare.com / raphaela.ns.cloudflare.com
#   - Cloudflare 站点(Zone): forcine.dpdns.org，zone id 见下
#   - Cloudflare Tunnel(命名): usagehub，id c8aef581-01ff-4da5-a150-7cca1f93c485
#   - DNS CNAME: usagehub.forcine.dpdns.org -> <tunnel-id>.cfargotunnel.com（proxied）
#   - 隧道采用「仪表盘托管 + token」模式：本地无需 cert.pem，也不生成 json 凭据
#
# 本机固定访问地址: https://usagehub.forcine.dpdns.org
#
# 用法：
#   ./scripts/start-tunnel.sh           # 同时起 usagehub serve + tunnel
#   ./scripts/start-tunnel.sh stop      # 停止这两个进程
#
# 依赖：本机已安装 cloudflared（~/.local/bin/cloudflared），且 ~/.cloudflared/tunnel-token.txt 存在

set -euo pipefail

CF_DIR="${HOME}/.cloudflared"
TOKEN_FILE="${CF_DIR}/tunnel-token.txt"
CLOUDFLARED="${HOME}/.local/bin/cloudflared"
[[ -x "$CLOUDFLARED" ]] || CLOUDFLARED="$(command -v cloudflared)"
PID_DIR="${CF_DIR}/run"
SERVE_PORT=8787
USAGEHUB_DIR="${HOME}/ProjectHub/Mine/src/UsageHub"

PID_SERVE="${PID_DIR}/serve.pid"
PID_TUNNEL="${PID_DIR}/tunnel.pid"

mkdir -p "$PID_DIR"

stop() {
  echo "==> 停止 serve / tunnel"
  [[ -f "$PID_TUNNEL" ]] && kill "$(cat "$PID_TUNNEL")" 2>/dev/null && rm -f "$PID_TUNNEL"
  [[ -f "$PID_SERVE" ]] && kill "$(cat "$PID_SERVE")" 2>/dev/null && rm -f "$PID_SERVE"
  # 兜底：按命令行特征清掉
  pkill -f "cloudflared tunnel run --token" 2>/dev/null || true
  echo "已停止"
  exit 0
}

[[ "${1:-}" == "stop" ]] && stop

if [[ ! -f "$TOKEN_FILE" ]]; then
  echo "缺少隧道 token: $TOKEN_FILE" >&2
  echo "请从 Cloudflare 仪表盘 Tunnel 详情页取 token，写入该文件（权限 600）。" >&2
  exit 1
fi
chmod 600 "$TOKEN_FILE"

echo "==> 启动 UsageHub serve (127.0.0.1:${SERVE_PORT})"
cd "$USAGEHUB_DIR"
nohup .venv/bin/python -m usagehub serve --lan >"${CF_DIR}/serve.log" 2>&1 &
echo $! >"$PID_SERVE"

echo "==> 启动 Cloudflare tunnel (usagehub.forcine.dpdns.org)"
TOKEN="$(cat "$TOKEN_FILE")"
nohup "$CLOUDFLARED" tunnel run --token "$TOKEN" --url "http://127.0.0.1:${SERVE_PORT}" >"${CF_DIR}/tunnel.log" 2>&1 &
echo $! >"$PID_TUNNEL"

sleep 4
echo "serve pid=$(cat "$PID_SERVE" 2>/dev/null)  tunnel pid=$(cat "$PID_TUNNEL" 2>/dev/null)"
echo "固定地址: https://usagehub.forcine.dpdns.org"
echo "（首次 NS 生效需等 DigitalPlat 委派传播，几分钟到几小时不等）"
