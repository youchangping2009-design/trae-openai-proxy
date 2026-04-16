"""基本测试：验证核心流程"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from main import app, extract_user_question

client = TestClient(app)

def test_health_check():
    """测试健康检查端点"""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "running"

def test_extract_user_question_simple():
    """测试提取用户问题 - 简单格式"""
    request_body = {
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "你好"}]}
        ]
    }
    assert extract_user_question(request_body) == "你好"

def test_extract_user_question_string_content():
    """测试提取用户问题 - 字符串内容"""
    request_body = {
        "input": [
            {"role": "user", "content": "测试问题"}
        ]
    }
    assert extract_user_question(request_body) == "测试问题"

def test_extract_user_question_invalid():
    """测试提取用户问题 - 无效格式应抛异常"""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        extract_user_question({"invalid": "format"})
    assert exc_info.value.status_code == 400

@patch('main.call_traecli')
def test_chat_completions(mock_traecli):
    """测试 Chat Completions 端点"""
    mock_traecli.return_value = "你好！我是AI助手"

    response = client.post("/v1/chat/completions", json={
        "model": "glm-5.1",
        "messages": [{"role": "user", "content": "你好"}]
    })

    assert response.status_code == 200
    data = response.json()
    assert data["choices"][0]["message"]["content"] == "你好！我是AI助手"
    assert data["usage"] is None  # 不返回假数据

@patch('main.call_traecli')
def test_responses_non_stream(mock_traecli):
    """测试 Responses API - 非流式"""
    mock_traecli.return_value = "测试回复"

    response = client.post("/v1/responses", json={
        "model": "glm-5.1",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "测试"}]}],
        "stream": False
    })

    assert response.status_code == 200
    data = response.json()
    assert data["output"][0]["content"][0]["text"] == "测试回复"
    assert data["usage"] is None  # 不返回假数据

def test_chat_completions_no_user_message():
    """测试 Chat Completions - 缺少用户消息应返回 400"""
    response = client.post("/v1/chat/completions", json={
        "model": "glm-5.1",
        "messages": [{"role": "system", "content": "系统提示"}]
    })

    assert response.status_code == 400
