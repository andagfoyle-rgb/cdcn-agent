"""
CDCNLLMClient - async LLM client supporting Ollama and OpenAI-compatible APIs.
Supports SiliconFlow (GLM-5) and Ollama. Embeddings always run locally.
"""
import asyncio
import json, logging, os, time
import httpx

from app.config import settings

_RETRYABLE_STATUSES = {429, 502, 503}
_RETRY_DELAYS = [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0, 30.0, 30.0]  # ~2.5 min total

log = logging.getLogger(__name__)

class CDCNLLMError(Exception):
    pass

def _get_provider():
    return os.environ.get("LLM_PROVIDER", "ollama").lower().strip()

class CDCNLLMClient:
    def __init__(self, base_url=None, model=None, api_key=None, provider=None):
        self.provider = provider or _get_provider()
        if self.provider == "ollama":
            self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
            self.model = model or settings.ollama_model
            self.api_key = api_key if api_key is not None else settings.ollama_api_key
        else:
            self.base_url = (base_url or os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.com/v1")).rstrip("/")
            self.model = model or os.environ.get("SILICONFLOW_MODEL", "zai-org/GLM-5")
            self.api_key = api_key or os.environ.get("SILICONFLOW_API_KEY", "")
        log.info("LLMClient init: provider=%s base_url=%s model=%s", self.provider, self.base_url, self.model)

    def configure(self, base_url, model):
        self.base_url = base_url.rstrip("/")
        self.model = model
        log.info("LLMClient reconfigured: base_url=%s model=%s", self.base_url, self.model)

    @property
    def _is_openai_compat(self):
        return self.provider != "ollama"

    @property
    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    # Rough token budget — leave headroom for the response (4096 tokens)
    _MAX_PROMPT_TOKENS = 180_000
    _CHARS_PER_TOKEN = 4  # conservative estimate

    def _estimate_tokens(self, text: str) -> int:
        return len(text) // self._CHARS_PER_TOKEN + 1

    def _trim_messages(self, messages: list[dict]) -> list[dict]:
        """Drop older messages to stay within the token budget.

        Always keeps at least the last 6 messages (3 exchanges) to maintain
        conversational coherence.  Drops from the front of the list.
        """
        budget = self._MAX_PROMPT_TOKENS
        MIN_KEEP = 6  # always keep at least the last N messages

        # Estimate total tokens
        total = sum(self._estimate_tokens(m.get("content") or "") for m in messages)
        if total <= budget:
            return messages

        log.warning(
            "Context trimming: %d messages, ~%dk tokens → trimming to fit %dk budget",
            len(messages), total // 1000, budget // 1000,
        )

        # Work backwards keeping messages until budget exceeded
        kept: list[dict] = []
        running = 0
        for msg in reversed(messages):
            cost = self._estimate_tokens(msg.get("content") or "")
            if running + cost > budget and len(kept) >= MIN_KEEP:
                break
            kept.append(msg)
            running += cost

        kept.reverse()
        log.info("Context trimmed: kept %d / %d messages (~%dk tokens)",
                 len(kept), len(messages), running // 1000)
        return kept

    def _build_messages(self, messages, system_prompt):
        trimmed = self._trim_messages(list(messages))
        if system_prompt:
            return [{"role": "system", "content": system_prompt}] + trimmed
        return trimmed

    def _chat_url(self):
        if self._is_openai_compat:
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/api/chat"

    async def _audit(self, action, *, prompt_tokens=0, completion_tokens=0, latency_ms=0, skill_used="", user_id="", role=""):
        try:
            from app.storage.audit_log import log_event, log_llm_call
            await log_event(actor="llm_client", action=action, target=self.model,
                detail=json.dumps({"model": self.model, "provider": self.provider,
                    "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                    "latency_ms": latency_ms, "skill_used": skill_used,
                    "user_id": user_id}))
            await log_llm_call(
                user_id=user_id, role=role, skill=skill_used,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                latency_ms=latency_ms, state=action,
            )
        except Exception as exc:
            log.warning("Audit log write failed: %s", exc)

        # Measure 4: Log to token_usage table for cost tracking
        try:
            from app.gateway.token_tracker import log_token_usage
            await log_token_usage(
                user_id=user_id,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                cached_tokens=0,  # SiliconFlow doesn't report cached tokens separately yet
                model=self.model,
                skill_used=skill_used,
            )
        except Exception:
            pass  # Never let tracking break the main flow

    async def chat_stream(self, messages, system_prompt=None, skill_used="", user_id="", role=""):
        payload = {"model": self.model, "messages": self._build_messages(messages, system_prompt), "stream": True, "max_tokens": 4096}
        t0 = time.monotonic()
        prompt_tokens = completion_tokens = 0
        try:
            last_err = None
            for attempt, delay in enumerate([0.0] + _RETRY_DELAYS):
                if delay:
                    log.warning("LLM stream retryable error, retrying in %.1fs (attempt %d/%d)", delay, attempt, len(_RETRY_DELAYS))
                    await asyncio.sleep(delay)
                last_err = None
                try:
                    async with httpx.AsyncClient(timeout=300) as client:
                        async with client.stream("POST", self._chat_url(), json=payload, headers=self._headers) as resp:
                            if resp.status_code in _RETRYABLE_STATUSES:
                                body = (await resp.aread()).decode()
                                last_err = CDCNLLMError(f"LLM returned {resp.status_code}: {body[:300]}")
                                log.warning("LLM returned retryable %s: %s", resp.status_code, body[:200])
                                continue
                            if resp.status_code != 200:
                                body = await resp.aread()
                                raise CDCNLLMError(f"LLM returned {resp.status_code}: {body.decode()[:300]}")
                            if self._is_openai_compat:
                                async for line in resp.aiter_lines():
                                    if not line or not line.startswith("data"):
                                        continue
                                    data_str = line.split(":", 1)[1].strip() if ":" in line else ""
                                    if data_str == "[DONE]":
                                        break
                                    if not data_str:
                                        continue
                                    try:
                                        chunk = json.loads(data_str)
                                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                                        # GLM-5 sends reasoning_content first, then content
                                        token = delta.get("content") or ""
                                        if token:
                                            yield token
                                        usage = chunk.get("usage")
                                        if usage:
                                            prompt_tokens = usage.get("prompt_tokens", 0)
                                            completion_tokens = usage.get("completion_tokens", 0)
                                    except json.JSONDecodeError:
                                        continue
                            else:
                                async for line in resp.aiter_lines():
                                    if not line:
                                        continue
                                    try:
                                        chunk = json.loads(line)
                                    except json.JSONDecodeError:
                                        continue
                                    token = chunk.get("message", {}).get("content", "")
                                    if token:
                                        yield token
                                    if chunk.get("done"):
                                        prompt_tokens = chunk.get("prompt_eval_count", 0)
                                        completion_tokens = chunk.get("eval_count", 0)
                                        break
                            return  # success
                except CDCNLLMError:
                    raise
                except httpx.ConnectError as exc:
                    last_err = CDCNLLMError(f"Cannot connect to LLM at {self.base_url}: {exc}")
                    log.warning("LLM connection failed, will retry: %s", exc)
                    continue
                except httpx.HTTPError as exc:
                    raise CDCNLLMError(f"HTTP error communicating with LLM: {exc}") from exc
            raise last_err or CDCNLLMError("LLM request failed after all retries")
        finally:
            latency_ms = int((time.monotonic() - t0) * 1000)
            try:
                await self._audit("chat_stream", prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, latency_ms=latency_ms, skill_used=skill_used, user_id=user_id, role=role)
            except Exception:
                pass

    async def chat(self, messages, system_prompt=None, skill_used="", user_id="", role=""):
        payload = {"model": self.model, "messages": self._build_messages(messages, system_prompt), "stream": False, "max_tokens": 4096}
        log.info("Making LLM call to %s model=%s provider=%s", self.base_url, self.model, self.provider)
        t0 = time.monotonic()
        prompt_tokens = completion_tokens = 0
        try:
            last_err = None
            for attempt, delay in enumerate([0.0] + _RETRY_DELAYS):
                if delay:
                    log.warning("LLM retryable error, retrying in %.1fs (attempt %d/%d)", delay, attempt, len(_RETRY_DELAYS))
                    await asyncio.sleep(delay)
                try:
                    async with httpx.AsyncClient(timeout=300) as client:
                        resp = await client.post(self._chat_url(), json=payload, headers=self._headers)
                        if resp.status_code in _RETRYABLE_STATUSES:
                            last_err = CDCNLLMError(f"LLM returned {resp.status_code}: {resp.text[:300]}")
                            log.warning("LLM returned retryable %s: %s", resp.status_code, resp.text[:200])
                            continue
                        if resp.status_code != 200:
                            raise CDCNLLMError(f"LLM returned {resp.status_code}: {resp.text[:300]}")
                        data = resp.json()
                        if self._is_openai_compat:
                            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                            usage = data.get("usage", {})
                            prompt_tokens = usage.get("prompt_tokens", 0)
                            completion_tokens = usage.get("completion_tokens", 0)
                        else:
                            content = data.get("message", {}).get("content", "")
                            prompt_tokens = data.get("prompt_eval_count", 0)
                            completion_tokens = data.get("eval_count", 0)
                        return content
                except CDCNLLMError:
                    raise
                except httpx.ConnectError as exc:
                    last_err = CDCNLLMError(f"Cannot connect to LLM at {self.base_url}: {exc}")
                    log.warning("LLM connection failed, will retry: %s", exc)
                    continue
                except httpx.HTTPError as exc:
                    raise CDCNLLMError(f"HTTP error communicating with LLM: {exc}") from exc
            raise last_err or CDCNLLMError("LLM request failed after all retries")
        finally:
            latency_ms = int((time.monotonic() - t0) * 1000)
            await self._audit("chat", prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, latency_ms=latency_ms, skill_used=skill_used, user_id=user_id, role=role)

    async def chat_with_tools(self, messages, system_prompt=None, tools=None, skill_used="", user_id="", role=""):
        """
        Non-streaming chat call that supports OpenAI function/tool calling.
        Returns dict: {"content": str, "tool_calls": list}
        tool_calls is a list of dicts: [{"name": str, "arguments": dict}]
        """
        payload = {
            "model": self.model,
            "messages": self._build_messages(messages, system_prompt),
            "stream": False,
            "max_tokens": 4096,
        }
        if tools and self._is_openai_compat:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        t0 = time.monotonic()
        prompt_tokens = completion_tokens = 0
        try:
            last_err = None
            for attempt, delay in enumerate([0.0] + _RETRY_DELAYS):
                if delay:
                    log.warning("LLM tools retryable error, retrying in %.1fs (attempt %d/%d)", delay, attempt, len(_RETRY_DELAYS))
                    await asyncio.sleep(delay)
                try:
                    async with httpx.AsyncClient(timeout=300) as client:
                        resp = await client.post(self._chat_url(), json=payload, headers=self._headers)
                        if resp.status_code in _RETRYABLE_STATUSES:
                            last_err = CDCNLLMError(f"LLM returned {resp.status_code}: {resp.text[:300]}")
                            log.warning("LLM returned retryable %s: %s", resp.status_code, resp.text[:200])
                            continue
                        if resp.status_code != 200:
                            raise CDCNLLMError(f"LLM returned {resp.status_code}: {resp.text[:300]}")
                        data = resp.json()
                        if self._is_openai_compat:
                            msg = data.get("choices", [{}])[0].get("message", {})
                            content = msg.get("content") or ""
                            raw_tool_calls = msg.get("tool_calls") or []
                            tool_calls = []
                            for tc in raw_tool_calls:
                                fn = tc.get("function", {})
                                name = fn.get("name", "")
                                try:
                                    arguments = json.loads(fn.get("arguments", "{}"))
                                except json.JSONDecodeError:
                                    arguments = {}
                                tool_calls.append({"id": tc.get("id", ""), "name": name, "arguments": arguments})
                            usage = data.get("usage", {})
                            prompt_tokens = usage.get("prompt_tokens", 0)
                            completion_tokens = usage.get("completion_tokens", 0)
                            return {"content": content, "tool_calls": tool_calls}
                        else:
                            # Ollama doesn't support tool calling - fall back to regular chat
                            content = data.get("message", {}).get("content", "")
                            return {"content": content, "tool_calls": []}
                except CDCNLLMError:
                    raise
                except httpx.ConnectError as exc:
                    last_err = CDCNLLMError(f"Cannot connect to LLM at {self.base_url}: {exc}")
                    log.warning("LLM connection failed, will retry: %s", exc)
                    continue
                except httpx.HTTPError as exc:
                    raise CDCNLLMError(f"HTTP error communicating with LLM: {exc}") from exc
            raise last_err or CDCNLLMError("LLM request failed after all retries")
        finally:
            latency_ms = int((time.monotonic() - t0) * 1000)
            await self._audit("chat_with_tools", prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, latency_ms=latency_ms, skill_used=skill_used, user_id=user_id, role=role)

    async def embed(self, text):
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "http://127.0.0.1:11434/api/embeddings",
                    json={"model": settings.ollama_embed_model, "prompt": text},
                )
                if resp.status_code != 200:
                    raise CDCNLLMError(f"Ollama embed returned {resp.status_code}: {resp.text[:300]}")
                return resp.json()["embedding"]
        except httpx.ConnectError as exc:
            raise CDCNLLMError(f"Cannot connect to Ollama for embeddings: {exc}") from exc
        except httpx.HTTPError as exc:
            raise CDCNLLMError(f"HTTP error on embed: {exc}") from exc
        finally:
            latency_ms = int((time.monotonic() - t0) * 1000)
            await self._audit("embed", latency_ms=latency_ms)

llm_client = CDCNLLMClient()

async def chat(messages, *, model=None, base_url=None, stream=False, **kwargs):
    if base_url or model:
        tmp = CDCNLLMClient(base_url=base_url, model=model, provider="ollama")
        return await tmp.chat(messages, **kwargs)
    return await llm_client.chat(messages, **kwargs)

async def embed(text, *, model=None):
    return await llm_client.embed(text)

async def dream_chat(messages):
    """Route dream-mode LLM calls through the singleton, which is already
    configured by StateManager (cloud provider stays on cloud during dream)."""
    return await llm_client.chat(messages)
