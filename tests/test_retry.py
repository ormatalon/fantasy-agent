import httpx
import openai
import pytest

from src.agent import orchestrator
from src.agent.orchestrator import AgentError, _invoke_with_retry


def rate_limit(message: str) -> openai.RateLimitError:
    response = httpx.Response(429, request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"))
    return openai.RateLimitError(message, response=response, body=None)


class FakeLLM:
    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "answer"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(orchestrator.time, "sleep", lambda _s: None)


def test_daily_quota_fails_fast_with_an_actionable_message():
    llm = FakeLLM([rate_limit("Rate limit exceeded: free-models-per-day")])
    with pytest.raises(AgentError, match="daily request limit"):
        _invoke_with_retry(llm, [])
    assert llm.calls == 1


def test_a_per_minute_rate_limit_is_retried():
    llm = FakeLLM([rate_limit("Rate limit exceeded: free-models-per-min")])
    assert _invoke_with_retry(llm, []) == "answer"
    assert llm.calls == 2


def test_openrouter_body_errors_are_still_retried():
    llm = FakeLLM([ValueError("Provider returned error: temporarily overloaded")])
    assert _invoke_with_retry(llm, []) == "answer"


def test_unrelated_errors_are_not_swallowed():
    with pytest.raises(ValueError, match="bad schema"):
        _invoke_with_retry(FakeLLM([ValueError("bad schema")]), [])
