#!/bin/zsh
# Mac 刷闪助手：统一启动入口；失败时保留窗口及诊断日志。
set -u
cd -- "${0:A:h}" || exit 1
LOG="$PWD/启动诊断.log"
exec > >(tee -a "$LOG") 2>&1
print -- "\n===== 启动检查 $(date '+%Y-%m-%d %H:%M:%S') ====="
fail() {
  print -- "\n启动失败：$1"
  print -- "请把这个文件发来：$LOG"
  print -- "按回车退出。"
  [[ ! -t 0 ]] || read -r _
  exit 1
}
if [[ "$(uname -s)" != 'Darwin' ]]; then fail '只能在 macOS 上运行'; fi
if [[ "$(uname -m)" != 'arm64' ]]; then fail '当前安装包仅支持 Apple Silicon (M1/M2/M3/M4)'; fi
# A verified current build can launch without invoking the compiler again.
APP='Mac刷闪助手.app/Contents/MacOS/ShinyHunt'
if [[ ! -x "$APP" || -f .ui-build-required || ! -f .build/native.sha256 ]] || ! /usr/bin/shasum -a 256 -c .build/native.sha256 >/dev/null 2>&1; then
  /usr/bin/xcrun --find swiftc >/dev/null 2>&1 || fail '未检测到 Swift 编译器，无法重新构建界面'
  print -- '正在编译原生界面…'
  /bin/zsh ./build_native.sh || fail 'Swift 界面编译失败；原有应用已保留，请查看上方报错'
fi
PYTHON=''
if [[ -f .runtime-python ]]; then
  configured_python="$(cat .runtime-python)"
  if [[ -x "$configured_python" ]] && "$configured_python" -c 'import numpy,PIL,AppKit,Quartz,Vision,ScreenCaptureKit,ApplicationServices' >/dev/null 2>&1; then
    PYTHON="$configured_python"
  fi
fi
if [[ -n "$PYTHON" ]]; then
  :
elif [[ -x .venv/bin/python ]]; then
  PYTHON="$PWD/.venv/bin/python"
else
  for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"; do
    if [[ -x "$candidate" ]] && "$candidate" -c 'import sys; assert sys.version_info >= (3,11)' 2>/dev/null; then
      PYTHON="$candidate"; break
    fi
  done
  [[ -n "$PYTHON" ]] || fail '缺少 Python 3.11+；请安装后重新打开启动脚本'
  print -- "创建 Python 环境：$PYTHON"
  "$PYTHON" -m venv .venv || fail '创建 Python 虚拟环境失败'
  PYTHON="$PWD/.venv/bin/python"
fi
if ! "$PYTHON" -c 'import numpy,PIL,AppKit,Quartz,Vision,ScreenCaptureKit,ApplicationServices' >/dev/null 2>&1; then
  print -- '正在安装 Python 依赖…'
  "$PYTHON" -m pip install -r requirements.txt || fail 'Python 依赖安装失败（请检查网络或依赖兼容性）'
fi
print -- "$PYTHON" > .runtime-python
print -- '启动 UI …'
/usr/bin/open "$PWD/Mac刷闪助手.app" || fail '无法打开应用，请查看启动诊断.log'
print -- '应用启动请求已发送。如界面闪退，请提供启动诊断.log 和系统弹窗内容。'
