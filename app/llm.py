"""LLM 客户端：OpenAI 兼容协议 + 离线降级。

调用方只需要 `client.chat(messages, tools=...)`，返回统一结构：
    {"content": str | None, "tool_calls": [{name, arguments}], "raw": dict}

换供应商（DeepSeek / OpenAI / 通义 / 内网 vLLM）只改 .env：
    LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .config import settings


class LLMError(Exception):
    """模型调用失败（超时/限流/上游错误），上层据此降级为"仅检索结果"。"""


@dataclass
class LLMReply:
    content: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_call(self) -> bool:
        return bool(self.tool_calls)


class OpenAICompatClient:
    """最小可用的 Chat Completions 客户端，只依赖标准库 urllib。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model or settings.llm_model
        self.timeout = timeout or settings.llm_timeout

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int = 1200,
    ) -> LLMReply:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # 4xx/5xx
            body = exc.read().decode("utf-8", errors="ignore")[:500]
            raise LLMError(f"模型服务返回 {exc.code}：{body}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"模型服务不可达：{exc.reason}") from exc
        except TimeoutError as exc:
            raise LLMError("模型服务超时") from exc

        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"模型返回结构异常：{str(data)[:300]}") from exc

        tool_calls: list[dict[str, Any]] = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments")}
            tool_calls.append({"id": call.get("id"), "name": fn.get("name"), "arguments": args})

        return LLMReply(content=message.get("content"), tool_calls=tool_calls, raw=data)

    def stream(self, messages: list[dict[str, Any]], max_tokens: int = 1200) -> Any:
        """流式输出（SSE）。返回逐块文本的生成器；上游异常抛 LLMError。"""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": settings.llm_temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raise LLMError(f"模型服务返回 {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"模型服务不可达：{exc.reason}") from exc

        def _iter() -> Any:
            with resp:
                for raw in resp:
                    line = raw.decode("utf-8", errors="ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                        delta = obj["choices"][0].get("delta", {}).get("content")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta

        return _iter()


def build_client() -> OpenAICompatClient | None:
    """有 Key 返回真实客户端，无 Key 返回 None（上层走离线规则大脑）。"""
    if not settings.has_llm:
        return None
    return OpenAICompatClient()
