"""统一 LLM 客户端: 封装 DCS Cloud 统一 API (OpenAI 兼容).

特性:
- 分级模型路由: simple -> deepseek-v4-pro; complex -> glm-5.2 -> claude-opus-4-8
- 处理推理模型 (deepseek-v4-pro / glm-5.2): 思考在 reasoning_content, 答案在 content;
  若 content 为空(思考耗尽 max_tokens)则自动加倍 max_tokens 重试一次.
- 自动重试 + 指数退避; 复杂任务主模型耗尽后回退备选模型.
- 记录每次调用 token 用量, 供成本统计.
"""
from __future__ import annotations
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

from . import config

log = logging.getLogger("omicagent.llm")


@dataclass
class LLMResponse:
    content: str
    model: str
    usage: dict = field(default_factory=dict)
    reasoning: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return int(self.usage.get("total_tokens", 0))


class LLMClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or config.API_KEY
        if not self.api_key:
            raise RuntimeError("OMICAGENT_API_KEY 未设置 (见 .env)")
        self.endpoint = config.CHAT_ENDPOINT
        self.timeout = config.REQUEST_TIMEOUT
        self.max_retries = config.MAX_RETRIES
        self.usage_log: list[dict] = []

    # ---- 公开接口 ----
    def complete(
        self,
        prompt: str,
        task_type: str = "simple",
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.3,
        models: Optional[list[str]] = None,
    ) -> LLMResponse:
        """按任务类型路由模型并完成一次补全.

        task_type: 'simple' | 'complex'
        models: 显式指定模型链, 覆盖默认路由
        """
        chain = models if models else config.model_chain(task_type)
        if max_tokens is None:
            max_tokens = config.MAX_TOKENS_COMPLEX if task_type == "complex" else config.MAX_TOKENS_SIMPLE

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        last_err: Optional[Exception] = None
        for idx, model in enumerate(chain):
            try:
                resp = self._call_with_retry(model, messages, max_tokens, temperature)
                log.info("LLM ok: model=%s tokens=%s", model, resp.total_tokens)
                return resp
            except Exception as e:
                last_err = e
                log.warning("模型 %s 全部重试失败: %s", model, e)
                if idx < len(chain) - 1:
                    log.info("回退到备选模型: %s", chain[idx + 1])
        raise RuntimeError(f"所有模型均失败: {last_err}")

    # ---- 内部 ----
    def _call_with_retry(self, model, messages, max_tokens, temperature) -> LLMResponse:
        last_err = None
        cur_max = max_tokens
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._call(model, messages, cur_max, temperature)
                # 推理模型 content 为空但 reasoning 非空 -> 思考耗尽 token, 加倍重试一次
                if not resp.content.strip() and resp.reasoning.strip():
                    log.info("content 为空(思考耗尽 token), 加倍 max_tokens 重试: %s", cur_max)
                    cur_max = min(cur_max * 2, 16384)
                    if cur_max <= max_tokens:
                        continue
                    resp2 = self._call(model, messages, cur_max, temperature)
                    if resp2.content.strip():
                        return resp2
                return resp
            except requests.HTTPError as e:
                last_err = e
                status = e.response.status_code if e.response is not None else None
                # 4xx (除 429) 一般重试无益, 直接跳出交由上层回退
                if status and 400 <= status < 500 and status != 429:
                    raise
                time.sleep(min(2 ** attempt, 16))
            except Exception as e:
                last_err = e
                time.sleep(min(2 ** attempt, 16))
        raise RuntimeError(f"重试耗尽: {last_err}")

    def _call(self, model, messages, max_tokens, temperature) -> LLMResponse:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        r = requests.post(self.endpoint, headers=headers, json=payload, timeout=self.timeout)
        if r.status_code >= 400:
            log.error("API %s: %s", r.status_code, r.text[:500])
        r.raise_for_status()
        data = r.json()
        choice = data["choices"][0]["message"]
        content = choice.get("content") or ""
        reasoning = choice.get("reasoning_content") or ""
        usage = data.get("usage", {}) or {}
        self.usage_log.append({"model": model, "usage": usage})
        return LLMResponse(
            content=content, reasoning=reasoning,
            model=data.get("model", model), usage=usage, raw=data,
        )

    # ---- 便捷方法 ----
    def complete_json(self, prompt: str, task_type: str = "complex", system: Optional[str] = None,
                      max_tokens: Optional[int] = None) -> dict:
        """要求模型返回 JSON 并解析 (容错: 提取首个 {...} 块)."""
        sys = (system or "") + "\n严格只输出一个 JSON 对象, 不要多余文字, 不要 markdown 代码围栏."
        resp = self.complete(prompt, task_type=task_type, system=sys, max_tokens=max_tokens, temperature=0.1)
        return _extract_json(resp.content)

    # ---- 流式 ----
    def complete_stream(self, prompt: str, task_type: str = "complex", system: Optional[str] = None,
                        max_tokens: Optional[int] = None, temperature: float = 0.3,
                        models: Optional[list[str]] = None, history: Optional[list[dict]] = None):
        """流式补全, 生成器逐 token yield.

        yield ("reasoning", token) 或 ("content", token) 或 ("done", full_text).
        history: 可选对话历史 (list of {role, content}), 覆盖单 prompt.
        """
        import json as _json
        chain = models if models else config.model_chain(task_type)
        if max_tokens is None:
            max_tokens = config.MAX_TOKENS_COMPLEX if task_type == "complex" else config.MAX_TOKENS_SIMPLE
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if history:
            messages.extend(history)
        else:
            messages.append({"role": "user", "content": prompt})

        last_err = None
        for idx, model in enumerate(chain):
            try:
                yield from self._stream_call(model, messages, max_tokens, temperature)
                return
            except Exception as e:
                last_err = e
                log.warning("流式模型 %s 失败: %s", model, e)
                if idx < len(chain) - 1:
                    log.info("回退: %s", chain[idx + 1])
        raise RuntimeError(f"所有模型流式均失败: {last_err}")

    def _stream_call(self, model, messages, max_tokens, temperature):
        import json as _json
        payload = {"model": model, "messages": messages, "stream": True,
                   "max_tokens": max_tokens, "temperature": temperature}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        full_content = ""
        with requests.post(self.endpoint, headers=headers, json=payload, timeout=self.timeout, stream=True) as r:
            r.raise_for_status()
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = _json.loads(data_str)
                except Exception:
                    continue
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                rc = delta.get("reasoning_content") or ""
                cc = delta.get("content") or ""
                if rc:
                    yield ("reasoning", rc)
                if cc:
                    full_content += cc
                    yield ("content", cc)
        yield ("done", full_content)

    def total_usage(self) -> dict:
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": len(self.usage_log)}
        for e in self.usage_log:
            u = e["usage"]
            totals["prompt_tokens"] += int(u.get("prompt_tokens", 0))
            totals["completion_tokens"] += int(u.get("completion_tokens", 0))
            totals["total_tokens"] += int(u.get("total_tokens", 0))
        return totals


def _extract_json(text: str) -> dict:
    """从模型输出中提取首个 JSON 对象 (容错)."""
    import re
    text = text.strip()
    # 去掉 markdown 围栏
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # 提取首个 {...}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    raise ValueError(f"无法解析 JSON: {text[:300]}")
