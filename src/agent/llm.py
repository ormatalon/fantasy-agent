"""OpenRouter calls: quota pre-check, chat completions with retries, errors as API error objects.

OpenRouter reports upstream failures as HTTP 200 with an `error` body and no `choices`; those are retried.
401 (key) and the daily quota are not. The key goes only to OpenRouter: never into a row, event or trace.
"""

import time

import httpx
import openai
from langsmith.wrappers import wrap_openai

from web.errors import ApiError

RETRIES = 2
BACKOFF = (2.0, 5.0)  # seconds before retry 1 and 2
http = httpx.Client(timeout=180)  # every OpenRouter request goes through it (tests swap in a fake)


def key_invalid() -> ApiError:
    return ApiError("key_invalid", 401, "OpenRouter refused your key. Replace it in Settings.", question_kept=True)


def provider_error() -> ApiError:
    return ApiError(
        "provider_error", 502, "Something went wrong, so there is no answer. Nothing was changed in Sleeper.",
        question_kept=True,
    )


def provider_busy() -> ApiError:
    """The upstream model is overloaded (503); same code as provider_error, a message the manager can act on."""
    return ApiError("provider_error", 502, "The model provider is busy right now. Try again in a minute.",
                    question_kept=True)


def quota_exceeded(model: str, remaining: int, limit: int) -> ApiError:
    return ApiError(
        "quota_exceeded", 429,
        "Daily limit reached for this model. Wait, choose another model, or add credits to your OpenRouter account.",
        remaining=remaining, limit=limit, model=model, resets_at=None, question_kept=True,
    )


def free_requests(base_url: str, key: str) -> dict | None:
    """OpenRouter's free-model counter {used, limit, remaining}; costs no request. None if the account has none."""
    try:
        r = http.get(f"{base_url.rstrip('/')}/key", headers={"Authorization": f"Bearer {key}"}, timeout=15)
    except httpx.HTTPError:
        raise provider_error() from None
    if r.status_code == 401:
        raise key_invalid()
    if r.status_code != 200:
        raise provider_error()
    try:
        return r.json()["data"].get("free_model_daily_requests")
    except ValueError:  # not JSON: e.g. a captive portal's HTML page
        raise provider_error() from None


def check_quota(base_url: str, key: str, model: str) -> None:
    """Fail fast before a question when a free model's daily quota is used up."""
    if not model.endswith(":free"):
        return
    counter = free_requests(base_url, key)
    if counter and counter["remaining"] <= 0:
        raise quota_exceeded(model, counter["remaining"], counter["limit"])


def client(base_url: str, key: str) -> openai.OpenAI:
    """OpenAI SDK client on OpenRouter. Traced only when LangSmith tracing is enabled by env."""
    return wrap_openai(openai.OpenAI(base_url=base_url, api_key=key, max_retries=0, timeout=180, http_client=http))


def complete(llm: openai.OpenAI, model: str, messages: list[dict], tools: list[dict] | None,
             base_url: str, key: str) -> tuple[dict, dict | None]:
    """One model turn: (assistant message as a dict, usage). Raises ApiError after retries."""
    kwargs = {"model": model, "messages": messages}
    if tools:
        kwargs |= {"tools": tools, "tool_choice": "auto"}
    for attempt in range(RETRIES + 1):
        overloaded = False  # the last attempt's failure was an upstream overload
        try:
            resp = llm.chat.completions.create(**kwargs)
        except openai.AuthenticationError:
            raise key_invalid() from None
        except openai.RateLimitError:
            _quota_or_retry(base_url, key, model)
        except openai.InternalServerError as e:
            overloaded = e.status_code == 503
        except openai.APIConnectionError:
            pass
        except openai.APIStatusError:
            raise provider_error() from None
        else:
            if resp.choices:
                msg = resp.choices[0].message
                out = {"content": msg.content or "", "tool_calls": [
                    {"id": c.id, "name": c.function.name, "arguments": c.function.arguments}
                    for c in msg.tool_calls or []
                ]}
                return out, resp.usage.model_dump(include={"prompt_tokens", "completion_tokens", "total_tokens"}) \
                    if resp.usage else None
            body = getattr(resp, "error", None) or {}  # HTTP 200 with an error body
            code = body.get("code") if isinstance(body, dict) else None
            if code == 401:
                raise key_invalid()
            if code == 429:
                _quota_or_retry(base_url, key, model)
            overloaded = code == 503
        if attempt < RETRIES:
            time.sleep(BACKOFF[attempt])
    raise provider_busy() if overloaded else provider_error()


def _quota_or_retry(base_url: str, key: str, model: str) -> None:
    """A 429 is the daily quota when the free counter is at 0; otherwise a short rate limit, retried."""
    counter = free_requests(base_url, key) if model.endswith(":free") else None
    if counter and counter["remaining"] <= 0:
        raise quota_exceeded(model, counter["remaining"], counter["limit"])
