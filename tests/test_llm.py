import openai
import pytest

from thai_shop_agent import llm


def rate_limit_error(message: str) -> openai.RateLimitError:
    """A RateLimitError without building an HTTP response (only its type and text matter here)."""
    e = openai.RateLimitError.__new__(openai.RateLimitError)
    Exception.__init__(e, message)
    return e


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(llm.time, "sleep", slept.append)
    return slept


def test_redact_hides_what_follows_api_key():
    msg = "Rate limit exceeded for api_key: 4314b1c5f933aeea. Limit type: tokens"
    assert llm.redact(msg) == "Rate limit exceeded for api_key: [redacted] Limit type: tokens"
    assert "sk-123" not in llm.redact("bad api key=sk-123")


def test_rate_limit_is_retried_and_wait_is_counted(no_sleep):
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise rate_limit_error("Rate limit exceeded")
        return "ok"

    before = llm.wait_seconds()
    assert llm.with_retry(flaky) == "ok"
    assert len(calls) == 3 and no_sleep == [15.0, 30.0]
    assert llm.wait_seconds() - before == pytest.approx(45.0)


def test_retry_gives_up_after_max_attempts(no_sleep):
    def always():
        raise rate_limit_error("Rate limit exceeded")

    with pytest.raises(openai.RateLimitError):
        llm.with_retry(always)
    assert len(no_sleep) == llm.MAX_ATTEMPTS - 1


def test_other_errors_are_not_retried(no_sleep):
    def bad():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        llm.with_retry(bad)
    assert no_sleep == []


def test_reset_time_in_message_sets_the_delay():
    e = rate_limit_error("Limit resets at: 2000-01-01 00:00:00 UTC")  # already passed -> minimum delay
    assert llm._retry_delay(e, 1) == 1.0
