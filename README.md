# trae-openai-proxy

将 [TraeCLI](https://github.com/nicepkg/trae-cli) 包装为 OpenAI 兼容 API，让 [OpenClaw](https://github.com/openclaw/openclaw) 等支持 OpenAI API 的客户端直接使用 TraeCLI 的模型能力，**无需额外配置模型 API Key**。

## 架构

```
用户 → OpenClaw Web (localhost:18789)
         ↓
    OpenClaw Gateway
         ↓ openai-responses API (SSE)
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

### 1. 安装依赖

```bash
cd ~/trae-openai-proxy
pip3 install -r requirements.txt
```

### 2. 启动代理服务

```bash
python3 main.py
```

服务默认监听 `http://127.0.0.1:8000`。

### 3. 配置 OpenClaw

编辑 `~/.openclaw/openclaw.json`，添加以下内容：

```json
{
  "gateway": {
    "mode": "local",
    "auth": { "mode": "none" }
  },
  "agents": {
    "defaults": {
      "compaction": { "reserveTokensFloor": 20000 },
      "llm": { "idleTimeoutSeconds": 300 }
    }
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
            "contextWindow": 32768,
            "maxTokens": 8192
          }
        ]
      }
    }
  }
}
```

> `apiKey` 设为任意值即可，代理不校验 Key。`contextWindow` 和 `maxTokens` 请根据 TraeCLI 实际使用的模型调整。

### 4. 启动 OpenClaw 网关

```bash
openclaw gateway run
```

### 5. 开始对话

浏览器打开 `http://127.0.0.1:18789/`，选择模型 `traecli/glm-5.1` 即可对话。

命令行测试：

```bash
openclaw infer model run --model traecli/glm-5.1 --prompt "1+2等于几？"
```

## 配置说明

代理服务的配置项集中在 `main.py` 顶部：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `TRAECLI_PATH` | `"traecli"` | TraeCLI 可执行文件路径 |
| `TRAECLI_TIMEOUT` | `300` | TraeCLI 调用超时（秒） |
| `DEFAULT_MODEL` | `"glm-5.1"` | 默认模型名称 |
| `HOST` | `"127.0.0.1"` | 监听地址 |
| `PORT` | `8000` | 监听端口 |

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/responses` | POST | OpenAI Responses API（OpenClaw 使用） |
| `/v1/chat/completions` | POST | OpenAI Chat Completions API（兼容其他客户端） |
| `/` | GET | 健康检查 |

### 请求示例

```bash
# Chat Completions 格式
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-5.1","messages":[{"role":"user","content":"你好"}]}'

# Responses 格式（非流式）
curl -X POST http://127.0.0.1:8000/v1/responses \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-5.1","input":[{"role":"user","content":[{"type":"input_text","text":"你好"}]}]}'
```

## 工作原理

1. OpenClaw 通过 `openai-responses` 适配器发送请求到代理
2. 代理从请求中提取用户实际问题（自动去除 OpenClaw 注入的系统提示和元数据）
3. 代理调用 TraeCLI 进行推理
4. 代理将 TraeCLI 的输出转换为 OpenAI Responses API 格式返回
5. 流式请求（`stream: true`）返回 SSE 事件序列，非流式请求返回完整 JSON

### SSE 事件序列

OpenClaw 使用 `openai-responses` 适配器，期望收到以下 SSE 事件：

```
1. response.created
2. response.in_progress
3. response.output_item.added
4. response.content_part.added
5. response.output_text.delta      ← 可多次发送，实现逐字输出
6. response.output_text.done
7. response.output_item.done
8. response.completed
```

## 常见问题

### Q: OpenClaw 显示 "Context limit exceeded"

在 `~/.openclaw/openclaw.json` 中确保已配置：

```json
"agents": {
  "defaults": {
    "compaction": { "reserveTokensFloor": 20000 }
  }
}
```

### Q: OpenClaw 显示 "outputs: 0" 无回复

确认代理服务正在运行（`curl http://127.0.0.1:8000/`），且 OpenClaw 配置中 `api` 字段为 `"openai-responses"`（不是 `"openai-completions"`）。

### Q: 请求超时

TraeCLI 首次调用可能较慢，可增大 `TRAECLI_TIMEOUT` 和 OpenClaw 的 `idleTimeoutSeconds`。

### Q: 会话文件锁错误

```bash
openclaw gateway stop
rm -f ~/.openclaw/agents/main/sessions/*.lock
openclaw gateway run
```

### Q: 切换 TraeCLI 使用的模型

编辑 `~/.trae/trae_cli.yaml`：

```yaml
model:
    name: GLM-5.1   # 或其他模型
```

然后同步更新 `~/.openclaw/openclaw.json` 中模型的 `id` 和 `name`。

## 项目结构

```
trae-openai-proxy/
├── main.py           # 代理服务主程序
└── requirements.txt  # Python 依赖
```

## 依赖

- **fastapi** — Web 框架
- **uvicorn** — ASGI 服务器
- **pydantic** — 请求模型校验

## License

MIT
