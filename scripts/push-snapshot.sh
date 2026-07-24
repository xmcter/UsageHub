#!/bin/bash
# 生成端到端加密快照并部署到 Vercel。由 launchd 定时调用（仅电脑开机时触发）。
# 数据全在本机，关机时无实时数据；本脚本让开机期间云端快照保持新鲜。
set -e
export PATH="/Users/a123/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cd /Users/a123/ProjectHub/Mine/src/UsageHub
exec .venv/bin/python -m usagehub snapshot --deploy
