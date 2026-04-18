#!/usr/bin/env bash

# 获取项目目录（不 cd，保持调用者的工作目录）
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PID_DIR="$PROJECT_DIR/logs"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

# ─── 工具函数 ───
check_port() {
    lsof -i ":$1" -t 2>/dev/null
}

kill_port() {
    local pids
    pids=$(check_port "$1")
    if [ -n "$pids" ]; then
        echo "  端口 $1 已被占用 (PID: $pids)，正在停止..."
        echo "$pids" | xargs kill 2>/dev/null
        sleep 1
        # 如果还没停，强制杀
        pids=$(check_port "$1")
        if [ -n "$pids" ]; then
            echo "$pids" | xargs kill -9 2>/dev/null
            sleep 1
        fi
    fi
}

# ─── 停止已有服务 ───
stop_services() {
    echo "停止服务..."
    # 通过 PID 文件停止
    for pidfile in "$PID_DIR"/*.pid; do
        [ -f "$pidfile" ] || continue
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            echo "  停止 PID $pid ($(basename "$pidfile" .pid))..."
            kill "$pid" 2>/dev/null
        fi
        rm -f "$pidfile"
    done
    # 通过端口停止（兜底）
    kill_port 8000
    kill_port 18789
    echo "服务已停止"
}

# ─── 启动服务 ───
start_services() {
    # 检查依赖
    command -v traecli >/dev/null || { echo "错误: 需要安装 traecli"; exit 1; }
    command -v openclaw >/dev/null || { echo "错误: 需要安装 openclaw"; exit 1; }
    command -v python3 >/dev/null || { echo "错误: 需要安装 python3"; exit 1; }

    # 激活 venv
    if [ -d "$PROJECT_DIR/venv" ]; then
        source "$PROJECT_DIR/venv/bin/activate"
    fi

    # 检查 Python 依赖
    if ! python3 -c "import fastapi, uvicorn, pydantic" 2>/dev/null; then
        echo "安装 Python 依赖..."
        python3 -m venv "$PROJECT_DIR/venv"
        source "$PROJECT_DIR/venv/bin/activate"
        pip3 install -q -r "$PROJECT_DIR/requirements.txt"
        pip3 install -q pytest httpx  # 测试依赖
    fi

    # 清空旧日志
    > "$LOG_DIR/proxy.log"
    > "$LOG_DIR/openclaw.log"

    # 启动代理
    if [ -n "$(check_port 8000)" ]; then
        echo "trae-openai-proxy: 端口 8000 已被占用，跳过（如需重启请先运行 $0 stop）"
    else
        echo "启动 trae-openai-proxy (http://127.0.0.1:8000)..."
        # 阻止系统睡眠（盒盖后仍保持运行）
        if command -v caffeinate >/dev/null 2>&1; then
            nohup caffeinate -i python3 "$PROJECT_DIR/main.py" >> "$LOG_DIR/proxy.log" 2>&1 &
        else
            nohup python3 "$PROJECT_DIR/main.py" >> "$LOG_DIR/proxy.log" 2>&1 &
        fi
        echo $! > "$PID_DIR/proxy.pid"

        # 等待代理就绪
        for i in $(seq 1 10); do
            if curl -s http://127.0.0.1:8000/ >/dev/null 2>&1; then
                break
            fi
            sleep 1
        done
        if ! curl -s http://127.0.0.1:8000/ >/dev/null 2>&1; then
            echo "错误: trae-openai-proxy 启动失败，查看日志: $LOG_DIR/proxy.log"
            cat "$LOG_DIR/proxy.log"
            stop_services
            exit 1
        fi
        echo "  trae-openai-proxy 已启动"
    fi

    # 启动 OpenClaw
    if [ -n "$(check_port 18789)" ]; then
        echo "OpenClaw: 端口 18789 已被占用，跳过（如需重启请先运行 $0 stop）"
    else
        echo "启动 OpenClaw (http://localhost:18789)..."
        if command -v caffeinate >/dev/null 2>&1; then
            nohup caffeinate -i openclaw gateway run >> "$LOG_DIR/openclaw.log" 2>&1 &
        else
            nohup openclaw gateway run >> "$LOG_DIR/openclaw.log" 2>&1 &
        fi
        echo $! > "$PID_DIR/openclaw.pid"

        # 等待 OpenClaw 就绪
        for i in $(seq 1 15); do
            if [ -n "$(check_port 18789)" ]; then
                break
            fi
            sleep 1
        done
        if [ -z "$(check_port 18789)" ]; then
            echo "警告: OpenClaw 可能启动失败，查看日志: $LOG_DIR/openclaw.log"
        else
            echo "  OpenClaw 已启动"
        fi
    fi

    echo ""
    echo "所有服务已启动（后台运行，关闭终端不影响）"
    echo "  - trae-openai-proxy: http://127.0.0.1:8000"
    echo "  - OpenClaw Web UI:   http://localhost:18789"
    echo "  - 日志目录: $LOG_DIR/"
    echo ""
    echo "停止服务: $0 stop"
    echo "查看状态: $0 status"
}

# ─── 查看状态 ───
status_services() {
    local running=0
    if [ -n "$(check_port 8000)" ]; then
        echo "trae-openai-proxy: 运行中 (PID $(check_port 8000), http://127.0.0.1:8000)"
        running=1
    else
        echo "trae-openai-proxy: 未运行"
    fi
    if [ -n "$(check_port 18789)" ]; then
        echo "OpenClaw: 运行中 (PID $(check_port 18789), http://localhost:18789)"
        running=1
    else
        echo "OpenClaw: 未运行"
    fi
    return $((1 - running))
}

# ─── 主逻辑 ───
case "${1:-}" in
    stop)
        stop_services
        ;;
    status)
        status_services
        ;;
    restart)
        stop_services
        sleep 1
        start_services
        ;;
    *)
        start_services
        ;;
esac
