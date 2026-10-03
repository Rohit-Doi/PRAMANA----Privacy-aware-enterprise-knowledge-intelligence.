"""LLM gateway: one interface over any OpenAI-compatible provider.

* An ordered fallback chain: when a provider is out of quota or down, the next one is tried.
* A provider that returned a quota error is skipped for a cool-down period.
* Identical requests are answered from a Redis cache.
* Every call is recorded (provider, model, tokens, latency) and counted per day.

The LLM is deliberately a small part of the system: it writes the answer and, for a few
uncertain claims, acts as a judge. Retrieval, access control and most verification are local.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import redis.asyncio as aioredis

from pramana.agents.models import LLMCall
from pramana.shared.config import Settings
from pramana.shared.crypto import sha256_hex
from pramana.shared.db import session_scope, utcnow
from pramana.shared.errors import DependencyUnavailable, LLMQuotaExhausted
from pramana.shared.logging import get_logger

log = get_logger(__name__)

_THOUGHT = re.compile(r"<thought>.*?</thought>|<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_UNCLOSED_THOUGHT = re.compile(r"^\s*<(?:thought|think)>.*", re.DOTALL | re.IGNORECASE)
_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

PROVIDER_BASE_URLS = {
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
}
QUOTA_COOLDOWN_SECONDS = 600
TRANSIENT_COOLDOWN_SECONDS = 45


@dataclass(frozen=True, slots=True)
class ProviderModel:
    provider: str
    model: str
    base_url: str
    api_key: str

    @property
    def name(self) -> str:
        return f"{self.provider}:{self.model}"

    @property
    def is_external(self) -> bool:
        return self.provider != "ollama"


@dataclass(frozen=True, slots=True)
class LLMResult:
    text: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cached: bool = False


def clean_output(text: str) -> str:
    """Remove the reasoning preamble some open models emit before their answer."""
    text = _THOUGHT.sub("", text)
    if _UNCLOSED_THOUGHT.match(text):
        return ""
    return text.strip()


def parse_json(text: str) -> Any:
    """Parse a JSON value from model output, tolerating code fences and surrounding prose."""
    stripped = _CODE_FENCE.sub("", text.strip()).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = stripped.find(opener), stripped.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("The model did not return valid JSON")


class LLMGateway:
    def __init__(self, settings: Settings, redis: aioredis.Redis) -> None:
        self._settings = settings
        self._redis = redis
        self._client = httpx.AsyncClient(timeout=settings.llm_timeout_seconds)
        self.chain = self._build_chain(settings)

    @staticmethod
    def _build_chain(settings: Settings) -> list[ProviderModel]:
        keys = {
            "google": settings.google_api_key,
            "groq": settings.groq_api_key,
            "cerebras": settings.cerebras_api_key,
        }
        chain: list[ProviderModel] = []
        for entry in settings.llm_chain.split(","):
            provider, _, model = entry.strip().partition(":")
            if not provider or not model:
                continue
            if provider == "ollama":
                if settings.ollama_base_url:
                    chain.append(ProviderModel("ollama", model, settings.ollama_base_url.rstrip("/") + "/v1", "ollama"))
                continue
            secret = keys.get(provider)
            if secret is None or not secret.get_secret_value():
                continue  # provider not configured: skip silently, it is optional
            chain.append(ProviderModel(provider, model, PROVIDER_BASE_URLS[provider], secret.get_secret_value()))
        return chain

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def configured(self) -> bool:
        return bool(self.chain)

    def has_local_provider(self) -> bool:
        return any(not p.is_external for p in self.chain)

    # ── Completion ──────────────────────────────────────────────────────
    async def complete(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        max_tokens: int = 900,
        temperature: float = 0.0,
        json_mode: bool = False,
        run_id: uuid.UUID | None = None,
        prompt_version: str = "",
        local_only: bool = False,
        use_cache: bool = True,
    ) -> LLMResult:
        if not self.chain:
            raise DependencyUnavailable("No LLM provider is configured (set GOOGLE_API_KEY)")
        cache_key = "llm:cache:" + sha256_hex(
            # The configured models are part of the key: a different model is a different answer.
            json.dumps(
                [system, user, max_tokens, temperature, json_mode, local_only, [p.name for p in self.chain]],
                ensure_ascii=False,
            )
        )
        if use_cache:
            cached = await self._cache_get(cache_key)
            if cached is not None:
                result = LLMResult(cached["text"], cached["provider"], cached["model"], 0, 0, 0, cached=True)
                await self._record(result, purpose, run_id, prompt_version, status="ok")
                return result

        candidates = [p for p in self.chain if not (local_only and p.is_external)]
        if not candidates:
            raise DependencyUnavailable("No local LLM is configured for content that may not leave this system")
        errors: list[str] = []
        quota_errors = 0
        for provider in candidates:
            if await self._cooling_down(provider):
                errors.append(f"{provider.name}: cooling down")
                quota_errors += 1
                continue
            started = time.perf_counter()
            try:
                text, tokens_in, tokens_out = await self._call(
                    provider, system, user, max_tokens, temperature, json_mode
                )
            except _QuotaError as exc:
                quota_errors += 1
                errors.append(f"{provider.name}: quota ({exc})")
                await self._set_cooldown(provider, exc.cooldown)
                await self._record_failure(provider, purpose, run_id, prompt_version, started, "quota", str(exc))
                continue
            except _ProviderError as exc:
                errors.append(f"{provider.name}: {exc}")
                await self._set_cooldown(provider, TRANSIENT_COOLDOWN_SECONDS)
                await self._record_failure(provider, purpose, run_id, prompt_version, started, "error", str(exc))
                continue
            if not text:
                errors.append(f"{provider.name}: empty response")
                await self._record_failure(provider, purpose, run_id, prompt_version, started, "empty", "")
                continue
            result = LLMResult(
                text,
                provider.provider,
                provider.model,
                tokens_in,
                tokens_out,
                int((time.perf_counter() - started) * 1000),
            )
            await self._record(result, purpose, run_id, prompt_version, status="ok")
            if use_cache:
                await self._cache_set(cache_key, result)
            return result

        log.error("llm.all_providers_failed", purpose=purpose, errors=errors)
        if quota_errors == len(candidates):
            raise LLMQuotaExhausted("Every configured LLM provider is out of quota. Try again later.")
        raise DependencyUnavailable("No LLM provider could answer: " + "; ".join(errors)[:400])

    async def complete_json(self, **kwargs: Any) -> tuple[Any, LLMResult]:
        """Completion parsed as JSON; one repair attempt if the first output is not valid JSON."""
        result = await self.complete(json_mode=True, **kwargs)
        try:
            return parse_json(result.text), result
        except ValueError:
            repair = dict(kwargs)
            repair["user"] = kwargs["user"] + "\n\nReturn ONLY valid JSON. No prose, no code fences."
            repair["use_cache"] = False
            result = await self.complete(json_mode=True, **repair)
            return parse_json(result.text), result

    async def _call(
        self, provider: ProviderModel, system: str, user: str, max_tokens: int, temperature: float, json_mode: bool
    ) -> tuple[str, int, int]:
        body: dict[str, Any] = {
            "model": provider.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode and not provider.model.startswith("gemma"):
            body["response_format"] = {"type": "json_object"}
        if provider.model.startswith("gemma"):
            body["max_tokens"] = max_tokens + 1500  # room for the reasoning preamble we strip
        try:
            response = await self._client.post(
                f"{provider.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {provider.api_key}"},
                json=body,
            )
        except httpx.TimeoutException as exc:
            raise _ProviderError("timeout") from exc
        except httpx.HTTPError as exc:
            raise _ProviderError(f"network error {type(exc).__name__}") from exc

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after", "")
            message = _error_message(response)
            daily = "per day" in message.lower() or "daily" in message.lower() or "PerDay" in message
            cooldown = QUOTA_COOLDOWN_SECONDS if daily else min(int(float(retry_after)) + 1 if retry_after else 30, 120)
            raise _QuotaError(message[:160], cooldown)
        if response.status_code >= 400:
            raise _ProviderError(f"HTTP {response.status_code}: {_error_message(response)[:160]}")
        try:
            payload = response.json()
            text = clean_output(payload["choices"][0]["message"].get("content") or "")
            usage = payload.get("usage") or {}
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise _ProviderError("malformed response") from exc
        return text, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)

    # ── Cooldown, cache, accounting ─────────────────────────────────────
    async def _cooling_down(self, provider: ProviderModel) -> bool:
        try:
            return bool(await self._redis.exists(f"llm:cooldown:{provider.name}"))
        except Exception:
            return False

    async def _set_cooldown(self, provider: ProviderModel, seconds: int) -> None:
        try:
            await self._redis.set(f"llm:cooldown:{provider.name}", "1", ex=max(1, seconds))
        except Exception as exc:
            log.warning("llm.cooldown_unavailable", error=repr(exc))

    async def _cache_get(self, key: str) -> dict[str, Any] | None:
        try:
            raw = await self._redis.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            return None

    async def _cache_set(self, key: str, result: LLMResult) -> None:
        try:
            await self._redis.set(
                key,
                json.dumps({"text": result.text, "provider": result.provider, "model": result.model}),
                ex=self._settings.llm_cache_ttl_seconds,
            )
        except Exception as exc:
            log.warning("llm.cache_unavailable", error=repr(exc))

    async def _record(
        self, result: LLMResult, purpose: str, run_id: uuid.UUID | None, prompt_version: str, *, status: str
    ) -> None:
        async with session_scope() as db:
            db.add(
                LLMCall(
                    run_id=run_id,
                    purpose=purpose,
                    provider=result.provider,
                    model=result.model,
                    prompt_version=prompt_version,
                    status=status,
                    cached=result.cached,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    latency_ms=result.latency_ms,
                    created_at=utcnow(),
                )
            )
        if not result.cached:
            try:
                day = datetime.now(UTC).strftime("%Y-%m-%d")
                key = f"llm:usage:{day}:{result.provider}:{result.model}"
                await self._redis.hincrby(key, "calls", 1)
                await self._redis.hincrby(key, "tokens", result.tokens_in + result.tokens_out)
                await self._redis.expire(key, 3 * 86_400)
            except Exception as exc:
                log.warning("llm.usage_counter_unavailable", error=repr(exc))

    async def _record_failure(
        self,
        provider: ProviderModel,
        purpose: str,
        run_id: uuid.UUID | None,
        prompt_version: str,
        started: float,
        status: str,
        error: str,
    ) -> None:
        async with session_scope() as db:
            db.add(
                LLMCall(
                    run_id=run_id,
                    purpose=purpose,
                    provider=provider.provider,
                    model=provider.model,
                    prompt_version=prompt_version,
                    status=status,
                    cached=False,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    error=error[:500],
                    created_at=utcnow(),
                )
            )

    async def usage_today(self) -> list[dict[str, Any]]:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        rows: list[dict[str, Any]] = []
        for provider in self.chain:
            usage = await self._redis.hgetall(f"llm:usage:{day}:{provider.provider}:{provider.model}")
            rows.append(
                {
                    "provider": provider.provider,
                    "model": provider.model,
                    "calls": int(usage.get("calls", 0)),
                    "tokens": int(usage.get("tokens", 0)),
                    "cooling_down": await self._cooling_down(provider),
                    "external": provider.is_external,
                }
            )
        return rows


class _ProviderError(Exception):
    pass


class _QuotaError(Exception):
    def __init__(self, message: str, cooldown: int) -> None:
        super().__init__(message)
        self.cooldown = cooldown


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message", ""))
        if isinstance(error, str):
            return error
    return str(payload)[:200]
