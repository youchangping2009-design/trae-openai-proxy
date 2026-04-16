# trae-openai-proxy

将 [TraeCLI](https://github.com/nicepkg/trae-cli) 包装为 OpenAI 兼容 API，让 [OpenClaw](https://github.com/openclaw/openclaw) 等客户端直接使用 TraeCLI 的模型能力。

## 架构

```
用户 → OpenClaw Web UI (localhost:18789)
         ↓
    OpenClaw Gateway
         ↓ HTTP (openai-responses API)
    trae-openai-proxy (localhost:8000)
         ↓ subprocess
    TraeCLI → 模型推理
```

## 前置条件

- Python 3.10+
- [TraeCLI](https://github.com/nicepkg/trae-cli) 已安装并登录
- [OpenClaw CLI](https://github.com/openclaw/openclaw) 已安装

验证 TraeCLI 可用：

```bash
traecli "你好" --print
```

## 快速开始

### 方式一：使用启动脚本（推荐）

```bash
cd ~/trae-openai-proxy
./start.sh
```

脚本会自动：
- 检查依赖并安装 Python 包
- 启动 trae-openai-proxy (http://127.0.0.1:8000)
- 启动 OpenClaw Gateway (http://localhost:18789)
- 按 Ctrl+C 停止所有服务

### 方式二：手动启动

```bash
# 1. 安装依赖
pip3 install -r requirements.txt

# 2. 启动代理
python3 main.py &

# 3. 启动 OpenClaw
openclaw gateway run &
```

### 配置 OpenClaw

首次使用需配置 OpenClaw。编辑 `~/.openclaw/openclaw.json`：

```json
{
  "gateway": {
    "mode": "local",
    "auth": { "mode": "none" }
  },
  "models": {
    "mode": "merge",
    "providers": {
      "traecli": {
        "baseUrl": "http://127.0.0.1:8000/v1",
        "apiKey": "dummy-key",
        "auth": "api-key",
        "api": "openai-responses",
        "models": [
          {
            "id": "glm-5.1",
            "name": "GLM-5.1 (via TraeCLI)",
            "api": "openai-responses",
            "reasoning": true,
            "contextWindow": 131072,
            "maxTokens": 16384
          }
        ]
      }
    }
  }
}
```

> **注意**：`apiKey` 可设为任意值，代理不校验。`contextWindow` 和 `maxTokens` 根据实际模型调整。

### 开始使用

浏览器打开 `http://localhost:18789`，选择模型 `traecli/glm-5.1` 即可对话。

命令行测试：

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-5.1","messages":[{"role":"user","content":"你好"}]}'
```

## 配置

代理服务配置项在 `main.py` 顶部：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `TRAECLI_PATH` | `"traecli"` | TraeCLI 可执行文件路径 |
| `TRAECLI_TIMEOUT` | `300` | TraeCLI 调用超时（秒） |
| `DEFAULT_MODEL` | `"glm-5.1"` | 默认模型名称 |
| `HOST` | `"127.0.0.1"` | 监听地址 |
| `PORT` | `8000` | 监听端口 |

## API 端点

| 端点 | 说明 |
|------|------|
| `POST /v1/responses` | OpenAI Responses API（OpenClaw 使用，支持流式） |
| `POST /v1/chat/completions` | OpenAI Chat Completions API（兼容其他客户端） |
| `GET /` | 健康检查 |

## 工作原理

1. OpenClaw 发送请求到代理（`openai-responses` 格式）
2. 代理提取用户问题
3. 代理调用 `traecli <prompt> --print` 获取回复
4. 代理将输出转换为 OpenAI Responses API 格式返回
5. 流式请求返回 SSE 事件序列，非流式返回完整 JSON

### SSE 事件序列

流式响应按以下顺序发送事件：

```
response.created → response.in_progress → response.output_item.added
→ response.content_part.added → response.output_text.delta
→ response.output_text.done → response.output_item.done → response.completed
```

## 测试

运行单元测试：

```bash
pytest test_main.py -v
```

测试覆盖：
- 健康检查
- 用户问题提取（多种格式）
- Chat Completions API
- Responses API（流式/非流式）
- 错误处理

## 常见问题

### OpenClaw 显示 "outputs: 0" 无回复

确认：
1. 代理正在运行：`curl http://127.0.0.1:8000/`
2. OpenClaw 配置中 `api` 字段为 `"openai-responses"`

### 请求超时

TraeCLI 首次调用可能较慢，增大超时：
- 代理：修改 `main.py` 中的 `TRAECLI_TIMEOUT`
- OpenClaw：在配置中添加 `"agents": {"defaults": {"llm": {"idleTimeoutSeconds": 600}}}`

### 切换模型

编辑 `~/.trae/trae_cli.yaml`：

```yaml
model:
  name: GLM-5.1  # 或其他模型
```

同步更新 OpenClaw 配置中的模型 `id` 和 `name`。

### 会话锁错误

```bash
openclaw gateway stop
rm -f ~/.openclaw/agents/main/sessions/*.lock
openclaw gateway run
```

## 项目结构

```
trae-openai-proxy/
├── main.py           # 代理服务（195 行）
├── test_main.py      # 单元测试（67 行）
├── start.sh          # 启动脚本（47 行）
├── stop.sh           # 停止脚本
└── requirements.txt  # Python 依赖
```

## 依赖

- **fastapi** — Web 框架
- **uvicorn** — ASGI 服务器
- **pydantic** — 数据验证
- **pytest** — 测试框架
- **httpx** — HTTP 客户端（测试用）

## 设计原则

本项目遵循 [Karpathy 的编码原则](https://x.com/karpathy/status/2015883857489522876)：

- **简单优先**：最小化代码，不做过度抽象
- **明确失败**：错误抛 HTTP 异常，不返回错误字符串
- **不伪造数据**：无法准确计算的 `usage` 字段返回 `null`
- **假设明确**：期望固定输入格式，格式错误返回 400

### 优化历史

从初始版本优化：
- 简化 `extract_user_question`：35 行 → 18 行
- 删除字符串匹配的元数据处理逻辑
- 错误处理改为抛异常（504/500）而非返回字符串
- 删除假的 token 计算（`len(text.split())`）
- 简化启动脚本：200 行 → 47 行
- 添加 7 个单元测试验证核心流程

## License

MIT
