#!/bin/bash
# 把状态栏程序打包成一个可双击的 macOS .app，装到 ~/Applications。
#
# 为什么需要它：`python -m usagehub menu` 是命令，只能在终端里敲。
# 包成 .app 之后可以双击启动、可以加进「登录项」开机自动挂、可以 Spotlight 搜到，
# 从此不用再碰终端。App 内部仍然只是拉起同一个 menu 程序，没有额外常驻服务。
#
# 重新生成：bash scripts/make-app.sh（覆盖式，改完代码不用重新打包，App 每次都读最新源码）

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python"
APP="$HOME/Applications/UsageHub.app"

if [ ! -x "$PY" ]; then
  echo "找不到虚拟环境: $PY" >&2
  echo "先在 $REPO 下建好 .venv 再跑本脚本。" >&2
  exit 1
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>UsageHub</string>
  <key>CFBundleDisplayName</key><string>UsageHub</string>
  <key>CFBundleIdentifier</key><string>com.xmcter.usagehub</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>UsageHub</string>
  <!-- 只在状态栏出现，不占 Dock、不抢 App 切换器 -->
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/UsageHub" <<LAUNCHER
#!/bin/bash
# 指向仓库里的源码，不复制副本——改完代码重开 App 就是新的，不用重新打包
cd "$REPO"
exec "$PY" -m usagehub menu
LAUNCHER

chmod +x "$APP/Contents/MacOS/UsageHub"

echo "✅ 已生成 $APP"
echo
echo "现在可以："
echo "  · 双击启动（Spotlight 搜 UsageHub 也能到）"
echo "  · 开机自动挂：系统设置 → 通用 → 登录项 → 「登录时打开」加号 → 选它"
echo "  · 取消开机挂：同一处减号移除即可"
