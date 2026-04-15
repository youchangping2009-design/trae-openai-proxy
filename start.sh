#!/usr/bin/env bash
set -euo pipefail

# ─── 配置 ───────────────────────────────────────────────
PROXY_HOST="127.0.0.1"
PROXY_PORT=8000
OPENCLAW_PORT=18789
# 获取脚本所在目录的绝对路径（支持从任意位置调用）
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_DIR="$PROJECT_DIR/.pids"
LOG_DIR="$PROJECT_DIR/.logs"

# ─── 颜色 ───────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ─── 清理函数 ───────────────────────────────────────────
cleanup() {
    echo ""
    info "正在停止所有服务..."
    if [ -d "$PID_DIR" ]; then
        for pidfile in "$PID_DIR"/*.pid; do
            [ -f "$pidfile" ] || continue
            local pid
            pid=$(cat "$pidfile")
            local name
            name=$(basename "$pidfile" .pid)
            if kill -0 "$pid" 2>/dev/null; then
                info "停止 $name (PID: $pid)..."
                kill "$pid" 2>/dev/null || true
            fi
            rm -f "$pidfile"
        done
    fi
    info "所有服务已停止"
    exit 0
}
trap cleanup EXIT INT TERM

# ─── 创建目录 ───────────────────────────────────────────
mkdir -p "$PID_DIR" "$LOG_DIR"

# ─── 检查依赖 ───────────────────────────────────────────
check_dependency() {
    local name="$1"
    local cmd="$2"
    local install_hint="$3"

    if ! command -v "$cmd" &>/dev/null; then
        error "找不到 $name"
        echo "  安装方式: $install_hint"
        return 1
    fi
    info "$name 已安装: $(command -v "$cmd")"
    return 0
}

info "========== 检查依赖 =========="

check_dependency "TraeCLI" "traecli" "访问 https://github.com/nicepkg/trae-cli 安装" || exit 1

if ! check_dependency "OpenClaw CLI" "openclaw" "brew install openclaw-cli"; then
    warn "正在自动安装 openclaw-cli..."
    brew install openclaw-cli
    if ! command -v openclaw &>/dev/null; then
        error "openclaw-cli 安装失败，请手动安装后重试"
        exit 1
    fi
    info "openclaw-cli 安装成功"
fi

if ! python3 -c "import fastapi, uvicorn, pydantic" 2>/dev/null; then
    warn "Python 依赖缺失，正在安装..."
    pip3 install -r "$PROJECT_DIR/requirements.txt"
fi

# ─── 检查端口占用 ───────────────────────────────────────
check_port() {
    local port="$1"
    lsof -i ":$port" -sTCP:LISTEN &>/dev/null
}

wait_for_port() {
    local port="$1"
    local name="$2"
    local max_wait=30
    local waited=0
    while ! check_port "$port"; do
        sleep 1
        waited=$((waited + 1))
        if [ $waited -ge $max_wait ]; then
            error "$name 启动超时（端口 $port 未就绪）"
            return 1
        fi
    done
    return 0
}

# ─── 停止已有服务 ───────────────────────────────────────
info "========== 清理旧进程 =========="
for pidfile in "$PID_DIR"/*.pid; do
    [ -f "$pidfile" ] || continue
    pid=$(cat "$pidfile")
    name=$(basename "$pidfile" .pid)
    if kill -0 "$pid" 2>/dev/null; then
        info "停止旧的 $name 进程 (PID: $pid)..."
        kill "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
done

# 如果端口被占用，尝试释放
for port in $PROXY_PORT $OPENCLAW_PORT; do
    if check_port "$port"; then
        warn "端口 $port 已被占用，尝试释放..."
        lsof -ti ":$port" | xargs kill 2>/dev/null || true
        sleep 1
    fi
done

# ─── 启动 trae-openai-proxy ─────────────────────────────
info "========== 启动 trae-openai-proxy =========="
cd "$PROJECT_DIR"
python3 main.py > "$LOG_DIR/proxy.log" 2>&1 &
PROXY_PID=$!
echo "$PROXY_PID" > "$PID_DIR/proxy.pid"
info "trae-openai-proxy 已启动 (PID: $PROXY_PID, http://$PROXY_HOST:$PROXY_PORT)"

if ! wait_for_port "$PROXY_PORT" "trae-openai-proxy"; then
    error "trae-openai-proxy 启动失败，查看日志: $LOG_DIR/proxy.log"
    cat "$LOG_DIR/proxy.log"
    exit 1
fi
info "trae-openai-proxy 已就绪"

# ─── 配置 OpenClaw ──────────────────────────────────────
OPENCLAW_CONFIG_DIR="$HOME/.openclaw"
OPENCLAW_CONFIG="$OPENCLAW_CONFIG_DIR/openclaw.json"

if [ ! -f "$OPENCLAW_CONFIG" ]; then
    info "首次使用，创建 OpenClaw 配置..."
    mkdir -p "$OPENCLAW_CONFIG_DIR"
    cat > "$OPENCLAW_CONFIG" <<EOF
{
  "providers": {
    "trae-proxy": {
      "baseUrl": "http://$PROXY_HOST:$PROXY_PORT/v1",
      "apiKey": "sk-trae-proxy",
      "api": "openai-responses",
      "models": {
        "glm-5.1": {}
      }
    }
  }
}
EOF
    info "OpenClaw 配置已创建: $OPENCLAW_CONFIG"
else
    info "OpenClaw 配置已存在: $OPENCLAW_CONFIG"
fi

# ─── 启动 OpenClaw ──────────────────────────────────────
info "========== 启动 OpenClaw =========="
openclaw gateway run > "$LOG_DIR/openclaw.log" 2>&1 &
OPENCLAW_PID=$!
echo "$OPENCLAW_PID" > "$PID_DIR/openclaw.pid"
info "OpenClaw 已启动 (PID: $OPENCLAW_PID, http://localhost:$OPENCLAW_PORT)"

if ! wait_for_port "$OPENCLAW_PORT" "OpenClaw"; then
    error "OpenClaw 启动失败，查看日志: $LOG_DIR/openclaw.log"
    cat "$LOG_DIR/openclaw.log"
    exit 1
fi
info "OpenClaw 已就绪"

# ─── 完成 ───────────────────────────────────────────────
echo ""
echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  所有服务已启动！${NC}"
echo -e "${CYAN}========================================${NC}"
echo -e "  trae-openai-proxy:  ${GREEN}http://$PROXY_HOST:$PROXY_PORT${NC}"
echo -e "  OpenClaw Web UI:    ${GREEN}http://localhost:$OPENCLAW_PORT${NC}"
echo ""
echo -e "  日志目录: $LOG_DIR/"
echo -e "    - proxy.log     (trae-openai-proxy 日志)"
echo -e "    - openclaw.log  (OpenClaw 日志)"
echo ""
echo -e "  按 ${YELLOW}Ctrl+C${NC} 停止所有服务"
echo ""

# 保持脚本运行，等待 Ctrl+C
wait
