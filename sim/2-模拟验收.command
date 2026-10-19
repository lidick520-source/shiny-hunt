#!/bin/zsh
# 用你 Mac 上的真实 OCR 跑模拟验收；只读截图，不操作游戏。
cd "${0:A:h}"
PY="$(cat ../.runtime-python 2>/dev/null || echo ../.venv/bin/python)"
"$PY" -B run_sim.py --live > sim_live.log 2>&1
echo finished >> sim_live.log
