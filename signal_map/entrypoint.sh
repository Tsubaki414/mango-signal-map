#!/bin/sh
set -eu

: "${SIGNAL_MAP_DB_PATH:=/data/signal_map.db}"
export SIGNAL_MAP_DB_PATH

# 只在卷是空的时候播种。
#
# 这一条是整个部署里最要紧的一行：客户在线上产生的会话、保留名单、提交记录
# 都写在这个卷里。每次部署都覆盖一遍，等于每次上线都把客户的选择清零。
if [ ! -f "$SIGNAL_MAP_DB_PATH" ]; then
  echo "[entrypoint] 卷里还没有库，用镜像里的种子播种一次。"
  mkdir -p "$(dirname "$SIGNAL_MAP_DB_PATH")"
  cp /app/seed/signal_map.seed.db "$SIGNAL_MAP_DB_PATH"
else
  echo "[entrypoint] 卷里已有库，保持不动（线上写入不会被部署覆盖）。"
fi

# SQLite 只能有一个写进程，所以**固定单 worker**。多开会互相锁死，
# 而且症状是间歇性的 500，很难查。要扩容得先换 Postgres。
exec uvicorn signal_map.backend.app:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 1
