#!/usr/bin/env bash
set -euo pipefail

# SafeDrones Stage-4 交互式演示（Unity 可视化 + 录屏）。
#
# 本脚本是 scripts/stage4_demo.py 的薄封装：演示逻辑已迁移到 Python 版本，
# 复用 stage4_benchmark 的构建/清理逻辑，支持 2 机交叉、5 机编队交叉、8 机 4v4 混战，
# 并自动使用项目 .venv 环境（旧版依赖的 `conda eai-swarm` 环境已废弃）。
#
# 用法与 stage4_demo.py 完全一致，例如：
#   bash scripts/stage4_demo.sh --scenario formation_crossing --condition C3 --seed 0
#   bash scripts/stage4_demo.sh --scenario four_v_four --condition C4 --seed 0
#   bash scripts/stage4_demo.sh --scenario four_v_four --condition C4 --seed 0 --non-interactive --hold-sec 8

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x "$ROOT_DIR/.venv/Scripts/python.exe" ]]; then
    PYTHON="$ROOT_DIR/.venv/Scripts/python.exe"
  elif command -v python >/dev/null 2>&1; then
    PYTHON="$(command -v python)"
  else
    echo "[demo] 未找到 Python，请先创建 .venv 或激活环境" >&2
    exit 2
  fi
fi

cd "$ROOT_DIR"
exec "$PYTHON" scripts/stage4_demo.py "$@"
