#!/bin/zsh
# 用 Mac 的 Apple Vision 给 frames/ 里每张真实截图录一次 OCR，写进 ocr_cache.json。
cd "${0:A:h}"
PY="$(cat ../.runtime-python 2>/dev/null || echo ../.venv/bin/python)"
"$PY" -B ocr_dump.py > ocr_dump.log 2>&1
echo finished >> ocr_dump.log
