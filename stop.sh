#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$SCRIPT_DIR/.pids"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $*"; }

if [ ! -d "$PID_DIR" ]; then
    info "没有运行中的服务"
    exit 0
fi

stopped=0
for pidfile in "$PID_DIR"/*.pid; do
    [ -f "$pidfile" ] || continue
    pid=$(cat "$pidfile")
    name=$(basename "$pidfile" .pid)
    if kill -0 "$pid" 2>/dev/null; then
        info "停止 $name (PID: $pid)..."
        kill "$pid" 2>/dev/null || true
        stopped=$((stopped + 1))
    else
        info "$name 已停止（清理残留 PID 文件）"
    fi
    rm -f "$pidfile"
done

# 兜底：如果 PID 文件丢失，通过端口清理
for port in 8000 18789; do
    pids=$(lsof -ti ":$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        info "端口 $port 仍有进程占用，正在清理..."
        echo "$pids" | xargs kill 2>/dev/null || true
    fi
done

rm -rf "$PID_DIR"

if [ $stopped -gt 0 ]; then
    info "已停止 $stopped 个服务"
else
    info "没有运行中的服务"
fi
