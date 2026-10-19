#!/bin/sh
# 单元测试：用 tools/stubs 代替 macOS 框架，所以任何电脑都能跑，也不会碰游戏。
cd "$(dirname "$0")/.." || exit 1
PY="${PYTHON:-python3}"
[ -f .runtime-python ] && [ -z "$PYTHON" ] && PY="$(cat .runtime-python)"
PYTHONPATH="$PWD/tools/stubs" "$PY" -B -m unittest discover -s . "$@"
