from __future__ import annotations

from types import SimpleNamespace

from anthropic import BadRequestError

from src.llm.claude_client import should_retry_exception


def test_should_retry_exception_false_for_insufficient_credits() -> None:
    response = SimpleNamespace(
        request=SimpleNamespace(),
        status_code=400,
        headers={"request-id": "req-test"},
    )
    exc = BadRequestError(
        message="Your credit balance is too low to access the Anthropic API.",
        response=response,
        body=None,
    )

    assert should_retry_exception(exc) is False


def test_should_retry_exception_true_for_transient_errors() -> None:
    exc = RuntimeError("temporary network issue")

    assert should_retry_exception(exc) is True
