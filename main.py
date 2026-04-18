from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import asyncio
import os
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
WORKSPACE_DIR = os.path.expanduser("~/.openclaw/workspace")  # traecli 工作目录

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
    """异步调用TraeCLI，失败时抛出异常"""
    def _run():
        result = subprocess.run(
            [TRAECLI_PATH, prompt, "--print"],
            capture_output=True,
            text=True,
            timeout=TRAECLI_TIMEOUT,
            check=True,
            cwd=WORKSPACE_DIR
        )
        return result.stdout.strip()

    try:
        return await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="TraeCLI 请求超时")
    except subprocess.CalledProcessError as e:
        logger.error("TraeCLI 错误: %s", e.stderr[:200])
        raise HTTPException(status_code=500, detail=f"TraeCLI 执行失败: {e.stderr[:100]}")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"找不到 TraeCLI (路径: {TRAECLI_PATH})")

# ─── 从OpenClaw请求中提取用户问题 ────────────────────────
def extract_user_question(request_body: dict) -> str:
    """从OpenAI Responses API格式的请求中提取用户问题

    假设格式: {"input": [{"role": "user", "content": [{"type": "input_text", "text": "..."}]}]}
    """
    try:
        input_list = request_body["input"]
        # 从后往前找最后一条用户消息
        for msg in reversed(input_list):
            if msg.get("role") == "user":
                content = msg["content"]
                if isinstance(content, str):
                    return content.strip()
                if isinstance(content, list):
                    for item in content:
                        if item.get("type") == "input_text":
                            return item["text"].strip()
        raise ValueError("未找到用户消息")
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"无效的请求格式: {e}")

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

    # 8. 响应完成（不返回 usage，因为无法准确计算）
    yield sse("response.completed", {
        "type": "response.completed",
        "response": {
            "id": resp_id, "object": "response", "created_at": created_at,
            "status": "completed", "model": model,
            "output": [{"type": "message", "id": msg_id, "role": "assistant", "content": [{"type": "output_text", "text": response_content}], "status": "completed"}],
            "usage": None
        }
    })

# ─── 非流式响应构建 ─────────────────────────────────────
def build_responses_api_response(response_content: str, model: str) -> dict:
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
        "usage": None  # TraeCLI 不提供 token 统计
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

    if not user_question:
        raise HTTPException(status_code=400, detail="未找到用户消息")

    response_content = await call_traecli(user_question)
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:12],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": response_content}, "finish_reason": "stop"}],
        "usage": None  # TraeCLI 不提供 token 统计
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
