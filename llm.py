"""
llm.py — Async LLM client for OpenRouter API.
Uses httpx.AsyncClient with exponential backoff, Retry-After support and model rotation.
"""

import asyncio
import logging
from typing import List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# ─────────────────────────── Constants ──────────────────────────────────────

API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Timeout for a single LLM request (connect, read)
REQUEST_TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=15.0)

# Status codes that apply to the whole account/key, not to one model.
# Rotating to another model cannot fix them, so we fail immediately.
FATAL_STATUS = {401: "API key không hợp lệ", 402: "Tài khoản OpenRouter hết credit"}

MAX_RETRY_AFTER = 30.0


class FatalLLMError(RuntimeError):
    """Error that no other model can recover from (bad key, no credit...)."""


def _backoff(attempt: int, response: Optional[httpx.Response] = None) -> float:
    """Exponential backoff, honouring a Retry-After header when present."""
    wait = float(2 ** (attempt + 1))
    if response is not None:
        raw = response.headers.get("retry-after")
        if raw:
            try:
                wait = max(wait, min(float(raw), MAX_RETRY_AFTER))
            except ValueError:
                pass
    return wait


# ─────────────────────────── Core LLM Call ──────────────────────────────────

async def call_llm(
    messages: List[dict],
    model: str,
    api_key: str,
    retries: int = 3,
) -> Tuple[str, dict]:
    """
    Call the OpenRouter chat completion API.

    Returns:
        (content, usage_dict) — assistant's reply and token usage dictionary.
    Raises:
        FatalLLMError for key/credit problems, RuntimeError for everything else.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://ai-code-agent.local",
        "X-Title": "AI Code Agent",
    }
    payload = {
        "model": model,
        "messages": messages,
    }

    last_error: Optional[Exception] = None

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for attempt in range(retries):
            try:
                response = await client.post(API_URL, json=payload, headers=headers)

                if response.status_code in FATAL_STATUS:
                    raise FatalLLMError(
                        f"{FATAL_STATUS[response.status_code]} "
                        f"(HTTP {response.status_code}): {response.text[:300]}"
                    )

                if response.status_code == 429 or response.status_code >= 500:
                    wait = _backoff(attempt, response)
                    last_error = RuntimeError(
                        f"HTTP {response.status_code} from {model}: {response.text[:200]}"
                    )
                    logger.warning(
                        "HTTP %d from %s (attempt %d/%d). Retrying in %.0fs…",
                        response.status_code, model, attempt + 1, retries, wait,
                    )
                    if attempt < retries - 1:
                        await asyncio.sleep(wait)
                    continue

                response.raise_for_status()
                data = response.json()

                choices = data.get("choices")
                if not choices:
                    raise RuntimeError(
                        f"No choices in response from {model}: {data}"
                    )

                content = choices[0].get("message", {}).get("content", "")
                if not content:
                    raise RuntimeError(
                        f"Empty content in response from {model}: {data}"
                    )

                raw_usage = data.get("usage", {})
                usage = {
                    "prompt_tokens": raw_usage.get("prompt_tokens", 0) or 0,
                    "completion_tokens": raw_usage.get("completion_tokens", 0) or 0,
                    "total_tokens": raw_usage.get("total_tokens", 0) or 0,
                }
                if usage["total_tokens"] == 0:
                    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]

                return content, usage

            except FatalLLMError:
                raise

            except httpx.TimeoutException as exc:
                last_error = exc
                wait = _backoff(attempt)
                logger.warning(
                    "Timeout calling %s (attempt %d/%d). Retrying in %.0fs…",
                    model, attempt + 1, retries, wait,
                )
                if attempt < retries - 1:
                    await asyncio.sleep(wait)

            except httpx.HTTPStatusError as exc:
                # Non-retryable HTTP error (4xx except 429 / fatal ones)
                raise RuntimeError(
                    f"HTTP {exc.response.status_code} from {model}: "
                    f"{exc.response.text[:500]}"
                ) from exc

            except Exception as exc:
                last_error = exc
                wait = _backoff(attempt)
                logger.warning(
                    "Error calling %s (attempt %d/%d): %s. Retrying in %.0fs…",
                    model, attempt + 1, retries, exc, wait,
                )
                if attempt < retries - 1:
                    await asyncio.sleep(wait)

    raise RuntimeError(
        f"Failed to call {model} after {retries} retries. Last error: {last_error}"
    )


# ─────────────────────────── Role Call with Rotation ────────────────────────

async def call_role(
    models_list: List[str],
    start_index: int,
    messages: List[dict],
    api_key: str,
) -> Tuple[str, str, List[str], dict]:
    """
    Try models in rotation starting from *start_index*.

    Returns:
        (reply, model_used, notes, usage_dict)
    """
    if not models_list:
        raise RuntimeError("No models configured for this role.")

    notes: List[str] = []
    n = len(models_list)

    for i in range(n):
        idx = (start_index + i) % n
        model = models_list[idx]

        try:
            reply, usage = await call_llm(messages, model, api_key)
            if i > 0:
                notes.append(f"Đã chuyển sang model {model} sau {i} lần thử thất bại.")
            return reply, model, notes, usage

        except FatalLLMError:
            # Same key / account for every model: rotating is pointless.
            raise

        except RuntimeError as exc:
            notes.append(f"Model {model} thất bại: {exc}")
            logger.warning("Model %s failed: %s", model, exc)
            continue

    raise RuntimeError(
        f"Tất cả {n} model đều thất bại. Chi tiết:\n" + "\n".join(notes)
    )
