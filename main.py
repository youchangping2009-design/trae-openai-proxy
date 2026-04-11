from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import asyncio
import subprocess
import json
import logging
import time
import uuid
from typing import List, Optional

app = FastAPI(title="TraeCLI OpenAI Proxy")
logger = logging.getLogger("trae-proxy")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ─── 配置 ───────────────────────────────────────────────
TRAECLI_PATH = "traecli"          # TraeCLI可执行文件路径
TRAECLI_TIMEOUT = 300             # TraeCLI调用超时（秒）
DEFAULT_MODEL = "glm-5.1"         # 默认模型名称
HOST = "127.0.0.1"
PORT = 8000

# ─── 请求模型 ───────────────────────────────────────────
class Message(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Message]
    temperature: Optional[float] = 1.0
    max_tokens: Optional[int] = None

# ─── 调用TraeCLI ────────────────────────────────────────
async def call_traecli(prompt: str) -> str:
    """异步调用TraeCLI，避免阻塞事件循环"""
    if not isinstance(prompt, str):
        prompt = str(prompt)

    def _run():
        try:
            result = subprocess.run(
                [TRAECLI_PATH, prompt, "--print"],
                capture_output=True,
                text=True,
                timeout=TRAECLI_TIMEOUT
            )
            if result.returncode != 0:
                logger.warning("TraeCLI stderr: %s", result.stderr[:200])
                return result.stderr.strip() or "TraeCLI返回错误"
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            return "请求超时，请稍后再试"
        except FileNotFoundError:
            return f"找不到TraeCLI（路径：{TRAECLI_PATH}），请确认已安装"
        except Exception as e:
            return f"调用TraeCLI出错: {e}"

    return await asyncio.to_thread(_run)

# ─── 从OpenClaw请求中提取用户问题 ────────────────────────
def extract_user_question(request_body: dict) -> str:
    """从OpenAI Responses API格式的请求中提取用户实际问题"""
    prompt = ""

    if "input" in request_body and isinstance(request_body["input"], list):
        for msg in reversed(request_body["input"]):
            if not isinstance(msg, dict):
                continue
            # {"type": "input_text", "text": "问题"}
            if msg.get("type") == "input_text" and "text" in msg:
                prompt = msg["text"]
                break
            # {"role": "user", "content": [{"type": "input_text", "text": "问题"}]}
            if msg.get("role") == "user" and "content" in msg:
                content = msg["content"]
                if isinstance(content, list):
                    for item in reversed(content):
                        if isinstance(item, dict) and item.get("type") == "input_text" and "text" in item:
                            prompt = item["text"]
                            break
                elif isinstance(content, str):
                    prompt = content
                break

    # 去除OpenClaw注入的Sender元数据，只保留用户实际输入
    if "Sender (untrusted metadata):" in prompt:
        # 元数据格式: Sender ...\n```json\n{...}\n```\n\n[时间戳] 实际问题
        lines = prompt.split("\n")
        for line in reversed(lines):
            line = line.strip()
            if line and not line.startswith("Sender") and not line.startswith("```") and not line.startswith("{") and not line.startswith("}"):
                prompt = line
                break

    return prompt.strip() or "Hello"

# ─── SSE流式响应生成器 ──────────────────────────────────
def generate_sse_stream(response_content: str, model: str):
    """生成符合OpenAI Responses API的SSE事件序列

    事件顺序（与OpenClaw的openai-responses适配器严格对应）：
    1. response.created       → 创建响应对象
    2. response.in_progress   → 响应进行中
    3. response.output_item.added   → 添加message输出项
    4. response.content_part.added  → 添加output_text内容部分
    5. response.output_text.delta   → 文本增量（可多次）
    6. response.output_text.done    → 文本完成
    7. response.output_item.done    → 输出项完成
    8. response.completed           → 响应完成
    """
    resp_id = "resp_" + uuid.uuid4().hex[:24]
    msg_id = "msg_" + uuid.uuid4().hex[:24]
    created_at = int(time.time())

    def sse(event: str, data: dict):
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    # 1-2. 创建响应 & 进行中
    resp_base = {"id": resp_id, "object": "response", "created_at": created_at, "status": "in_progress", "model": model, "output": [], "usage": None}
    yield sse("response.created", {"type": "response.created", "response": resp_base})
    yield sse("response.in_progress", {"type": "response.in_progress", "response": resp_base})

    # 3. 添加message输出项
    yield sse("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "id": msg_id, "role": "assistant", "content": [], "status": "in_progress"}})

    # 4. 添加output_text内容部分
    yield sse("response.content_part.added", {"type": "response.content_part.added", "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": ""}})

    # 5. 文本增量
    yield sse("response.output_text.delta", {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": response_content})

    # 6. 文本完成
    yield sse("response.output_text.done", {"type": "response.output_text.done", "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": response_content}})

    # 7. 输出项完成
    yield sse("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": {"type": "message", "id": msg_id, "role": "assistant", "content": [{"type": "output_text", "text": response_content}], "status": "completed"}})

    # 8. 响应完成
    out_tokens = max(len(response_content.split()), 1)
    yield sse("response.completed", {
        "type": "response.completed",
        "response": {
            "id": resp_id, "object": "response", "created_at": created_at,
            "status": "completed", "model": model,
            "output": [{"type": "message", "id": msg_id, "role": "assistant", "content": [{"type": "output_text", "text": response_content}], "status": "completed"}],
            "usage": {"input_tokens": 10, "output_tokens": out_tokens, "total_tokens": 10 + out_tokens}
        }
    })

# ─── 非流式响应构建 ─────────────────────────────────────
def build_responses_api_response(response_content: str, model: str) -> dict:
    out_tokens = max(len(response_content.split()), 1)
    return {
        "id": "resp_" + uuid.uuid4().hex[:24],
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": [{
            "type": "message",
            "id": "msg_" + uuid.uuid4().hex[:24],
            "role": "assistant",
            "content": [{"type": "output_text", "text": response_content}],
            "status": "completed"
        }],
        "usage": {"input_tokens": 10, "output_tokens": out_tokens, "total_tokens": 10 + out_tokens}
    }

# ─── API端点 ────────────────────────────────────────────

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """兼容OpenAI Chat Completions API（供非OpenClaw客户端使用）"""
    user_question = ""
    for msg in reversed(request.messages):
        if msg.role == "user":
            user_question = msg.content
            break

    response_content = await call_traecli(user_question)
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:12],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": response_content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    }

@app.post("/v1/responses")
async def responses(request: Request):
    """OpenAI Responses API端点（OpenClaw使用此端点）"""
    request_body = await request.json()
    is_stream = request_body.get("stream", False)
    model = request_body.get("model", DEFAULT_MODEL)

    user_question = extract_user_question(request_body)
    logger.info("Q: %s | stream=%s", user_question[:100], is_stream)

    response_content = await call_traecli(user_question)
    logger.info("A: %s", response_content[:100])

    if is_stream:
        return StreamingResponse(
            generate_sse_stream(response_content, model),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
        )
    return build_responses_api_response(response_content, model)

@app.get("/")
async def root():
    return {"service": "trae-openai-proxy", "status": "running"}

# ─── 启动 ───────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
