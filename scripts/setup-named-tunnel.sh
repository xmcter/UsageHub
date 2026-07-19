#!/usr/bin/env bash
# UsageHub Cloudflare 命名隧道配置（不装开机自启）
# 前置：
#   1) DigitalPlat 已注册免费域名，NS 已指到 Cloudflare
#   2) 域名已在 Cloudflare 添加为站点（Active）
#   3) 本机已安装 cloudflared
#
# 用法：
#   ./scripts/setup-named-tunnel.sh usagehub.example.dpdns.org
#
# 本脚本只：登录（若需要）/ 创建隧道 / 写配置 / 绑 DNS。
# 不安装 launchd、不后台常驻。需要访问时手动启动 serve + tunnel。
set -euo pipefail

DOMAIN="${1:-}"
if [[ -z "$DOMAIN" ]]; then
  echo "用法: $0 <完整域名，如 usagehub.xxx.dpdns.org>"
  exit 1
fi

TUNNEL_NAME="usagehub"
CF_DIR="${HOME}/.cloudflared"
CONF="${CF_DIR}/usagehub-config.yml"
CLOUDFLARED="${HOME}/.local/bin/cloudflared"
[[ -x "$CLOUDFLARED" ]] || CLOUDFLARED="$(command -v cloudflared)"

mkdir -p "$CF_DIR"

if [[ ! -f "${CF_DIR}/cert.pem" ]]; then
  echo "==> 首次需要登录 Cloudflare（会打开浏览器）"
  "$CLOUDFLARED" tunnel login
fi

if ! "$CLOUDFLARED" tunnel list 2>/dev/null | grep -q "$TUNNEL_NAME"; then
  echo "==> 创建隧道 $TUNNEL_NAME"
  "$CLOUDFLARED" tunnel create "$TUNNEL_NAME"
else
  echo "==> 隧道 $TUNNEL_NAME 已存在"
fi

TUNNEL_ID="$("$CLOUDFLARED" tunnel list | awk -v n="$TUNNEL_NAME" '$2==n {print $1; exit}')"
if [[ -z "$TUNNEL_ID" ]]; then
  echo "无法解析 tunnel id"
  exit 1
fi
CRED="${CF_DIR}/${TUNNEL_ID}.json"
if [[ ! -f "$CRED" ]]; then
  CRED="$(ls "${CF_DIR}"/*"${TUNNEL_ID}"*.json 2>/dev/null | head -1 || true)"
fi
if [[ -z "${CRED}" || ! -f "$CRED" ]]; then
  echo "找不到隧道凭据 json（~/.cloudflared/${TUNNEL_ID}.json）"
  exit 1
fi

echo "==> 写配置 ${CONF}"
cat >"$CONF" <<EOF
tunnel: ${TUNNEL_ID}
credentials-file: ${CRED}

ingress:
  - hostname: ${DOMAIN}
    service: http://127.0.0.1:8787
  - service: http_status:404
EOF

echo "==> 绑定 DNS ${DOMAIN} -> 隧道"
"$CLOUDFLARED" tunnel route dns "$TUNNEL_NAME" "$DOMAIN" || true

cat <<EOF

✅ 配置完成（未安装任何开机自启）

固定地址: https://${DOMAIN}
本地面板: http://127.0.0.1:8787

需要访问时手动开两个进程（两个终端，或自行用 tmux）：

  cd /Users/a123/ProjectHub/Mine/src/UsageHub
  .venv/bin/python -m usagehub serve --lan

  cloudflared tunnel --config ${CONF} run ${TUNNEL_NAME}

临时公网（地址会变，无需域名）：

  cloudflared tunnel --url http://127.0.0.1:8787

若 DNS 未生效：Cloudflare 仪表盘确认 ${DOMAIN} 的 CNAME 指向 ${TUNNEL_ID}.cfargotunnel.com
EOF
