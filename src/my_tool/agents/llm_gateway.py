"""LLM 网关（智能体编排层 · ★核心复用模块）。

全系统只允许本模块直接调用外部大模型 API；换供应商只改 PROVIDERS 表或环境变量，业务代码不动。
对应《架构选型》四层中的智能体编排层，阶段2 由 FastAPI /chat 直接复用。
"""

import os

import numpy as np
from openai import OpenAI  # 主流供应商均兼容 OpenAI 接口，换供应商只改配置

# ============================================================
# 供应商配置表：换供应商只改这里（或用环境变量覆盖），代码不动
# ============================================================
PROVIDERS = {
    # 硅基流动：同时提供对话与向量模型，默认选项
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "chat_model": "deepseek-ai/DeepSeek-V3",
        "embed_model": "BAAI/bge-m3",
    },
    # DeepSeek：只有对话模型，向量需用 EMBED_BASE_URL / EMBED_MODEL 另配
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "chat_model": "deepseek-chat",
        "embed_model": "",
    },
    # 智谱 GLM
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "chat_model": "glm-4-flash",
        "embed_model": "embedding-3",
    },
}


def load_config() -> tuple:
    """从环境变量读取配置，返回 (对话配置, 向量配置) 两套参数。

    环境变量：LLM_PROVIDER（供应商名）、LLM_API_KEY（必填）、
    BASE_URL / CHAT_MODEL / EMBED_BASE_URL / EMBED_MODEL / EMBED_API_KEY（可选覆盖）。
    """
    p = PROVIDERS[os.environ.get("LLM_PROVIDER", "siliconflow")]
    chat_cfg = {
        "base_url": os.environ.get("BASE_URL", p["base_url"]),
        "model": os.environ.get("CHAT_MODEL", p["chat_model"]),
        "api_key": os.environ["LLM_API_KEY"],  # 未设置时直接 KeyError，尽早暴露配置问题
    }
    embed_cfg = {
        "base_url": os.environ.get("EMBED_BASE_URL", chat_cfg["base_url"]),
        "model": os.environ.get("EMBED_MODEL", p["embed_model"]),
        "api_key": os.environ.get("EMBED_API_KEY", chat_cfg["api_key"]),
    }
    if not embed_cfg["model"]:
        raise SystemExit("该供应商未配置向量模型，请设置 EMBED_BASE_URL 和 EMBED_MODEL")
    return chat_cfg, embed_cfg


def llm_chat(prompt: str, chat_cfg: dict) -> str:
    """调用对话模型，返回回答文本。temperature=0 让回答稳定、便于验收。"""
    client = OpenAI(base_url=chat_cfg["base_url"], api_key=chat_cfg["api_key"])
    resp = client.chat.completions.create(
        model=chat_cfg["model"],
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    return resp.choices[0].message.content


def llm_chat_stream(prompt: str, chat_cfg: dict):
    """流式调用对话模型，逐段产出文本增量（★核心复用模块）。

    /chat 接口的打字机效果依赖本函数；每个 yield 是一小段文字，
    接口层把它包装成 SSE 的 delta 事件推给前端。
    timeout=30 秒：超时抛 APITimeoutError，由接口层转成中文错误信息。
    """
    client = OpenAI(base_url=chat_cfg["base_url"], api_key=chat_cfg["api_key"])
    stream = client.chat.completions.create(
        model=chat_cfg["model"],
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        stream=True,
        timeout=30,
    )
    for chunk in stream:
        # 每个chunk可能不含正文（如只带角色信息），先判空再取增量
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def llm_agent_step(messages: list, chat_cfg: dict, tools: list):
    """带工具表的单步对话（阶段4 Agent 循环用，非流式便于解析 tool_calls）。

    传入完整消息列表（含 system / tool 结果）与 TOOL_SCHEMAS，返回 assistant
    消息对象：要么含 content（最终回答），要么含 tool_calls（模型要求调工具）。
    免费模型不支持 tools 参数时会抛异常，由调用方降级回固定流程。
    """
    client = OpenAI(base_url=chat_cfg["base_url"], api_key=chat_cfg["api_key"])
    resp = client.chat.completions.create(
        model=chat_cfg["model"],
        messages=messages,
        temperature=0,
        tools=tools,
        timeout=30,
    )
    return resp.choices[0].message


def embed_texts(texts: list, embed_cfg: dict) -> np.ndarray:
    """把一批文本转向量，返回 numpy 矩阵（每行一个向量，已做 L2 归一化）。

    归一化后用内积即等价余弦相似度，与 kb_service 的 FAISS 内积索引配套。
    """
    client = OpenAI(base_url=embed_cfg["base_url"], api_key=embed_cfg["api_key"])
    resp = client.embeddings.create(model=embed_cfg["model"], input=texts)
    vecs = np.array([d.embedding for d in resp.data], dtype="float32")
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.maximum(norms, 1e-10)
