#!/usr/bin/env bash
set -euo pipefail

# 获取项目目录
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# 检查依赖
command -v traecli >/dev/null || { echo "错误: 需要安装 traecli"; exit 1; }
command -v openclaw >/dev/null || { echo "错误: 需要安装 openclaw"; exit 1; }
command -v python3 >/dev/null || { echo "错误: 需要安装 python3"; exit 1; }

# 检查 Python 依赖
if ! python3 -c "import fastapi, uvicorn, pydantic" 2>/dev/null; then
    echo "安装 Python 依赖..."
    pip3 install -r requirements.txt
fi

# 清理函数
cleanup() {
    echo "停止服务..."
    jobs -p | xargs -r kill 2>/dev/null || true
    exit 0
}
trap cleanup EXIT INT TERM

# 启动代理
echo "启动 trae-openai-proxy (http://127.0.0.1:8000)..."
python3 main.py &

# 等待代理就绪
sleep 2
if ! curl -s http://127.0.0.1:8000/ >/dev/null; then
    echo "错误: trae-openai-proxy 启动失败"
    exit 1
fi

# 启动 OpenClaw
echo "启动 OpenClaw (http://localhost:18789)..."
openclaw gateway run &

sleep 2
echo ""
echo "所有服务已启动"
echo "  - trae-openai-proxy: http://127.0.0.1:8000"
echo "  - OpenClaw Web UI:   http://localhost:18789"
echo ""
echo "按 Ctrl+C 停止"

wait
