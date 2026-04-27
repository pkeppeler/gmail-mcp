"""Unit tests for the batch retry logic in gmail_client."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]
from httplib2 import Response  # type: ignore[import-untyped]

from gmail_mcp.gmail_client import _execute_with_transport_retry, _run_batch


def _http_error(status: int) -> HttpError:
    return HttpError(Response({"status": str(status)}), b"error", uri="http://x")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent real sleeps in all tests."""
    monkeypatch.setattr("gmail_mcp.gmail_client.time.sleep", lambda _: None)


def _make_svc(
    responses: dict[str, dict[str, Any] | Exception],
    batch_sizes: list[int] | None = None,
) -> MagicMock:
    """Mock Gmail service that invokes batch callbacks with predetermined responses.

    Pass a dict of {request_id: response_dict_or_Exception}. When the batch
    executes, each added request gets its corresponding response via the callback.
    If batch_sizes is provided, appends the size of each executed batch to it.
    """
    svc = MagicMock()

    def _new_batch(callback: Any = None) -> MagicMock:
        batch = MagicMock()
        added: list[str] = []

        def _add(request: Any, request_id: str = "") -> None:
            added.append(request_id)

        def _execute() -> None:
            if batch_sizes is not None:
                batch_sizes.append(len(added))
            for rid in added:
                resp = responses.get(rid)
                if isinstance(resp, Exception):
                    callback(rid, {}, resp)
                else:
                    callback(rid, resp, None)

        batch.add = _add
        batch.execute = _execute
        return batch

    svc.new_batch_http_request = _new_batch
    return svc


# ---------------------------------------------------------------------------
# _execute_with_transport_retry
# ---------------------------------------------------------------------------


class TestTransportRetry:
    def test_succeeds_immediately(self) -> None:
        batch = MagicMock()
        _execute_with_transport_retry(batch)
        batch.execute.assert_called_once()

    @pytest.mark.parametrize("failures_before_success", [1, 2])
    def test_retries_then_succeeds(self, failures_before_success: int) -> None:
        batch = MagicMock()
        batch.execute.side_effect = [
            *[OSError("fail")] * failures_before_success,
            None,
        ]

        _execute_with_transport_retry(batch)

        assert batch.execute.call_count == failures_before_success + 1

    def test_raises_after_exhausting_retries(self) -> None:
        batch = MagicMock()
        batch.execute.side_effect = OSError("persistent")

        with pytest.raises(OSError, match="persistent"):
            _execute_with_transport_retry(batch)

        assert batch.execute.call_count == 3


# ---------------------------------------------------------------------------
# _run_batch
# ---------------------------------------------------------------------------


class TestRunBatch:
    def test_all_succeed(self) -> None:
        responses = {"a": {"id": "a"}, "b": {"id": "b"}}
        result = _run_batch(_make_svc(responses), ["a", "b"], MagicMock())
        assert result == responses

    def test_non_retryable_errors_are_dropped(self) -> None:
        responses: dict[str, dict[str, Any] | Exception] = {
            "ok": {"id": "ok"},
            "bad": _http_error(404),
        }
        result = _run_batch(_make_svc(responses), ["ok", "bad"], MagicMock())
        assert "ok" in result
        assert "bad" not in result

    def test_retryable_error_is_retried_and_succeeds(self) -> None:
        """First call returns 429 for 'flaky'; second call succeeds."""
        attempt = {"n": 0}

        svc = MagicMock()

        def _new_batch(callback: Any = None) -> MagicMock:
            batch = MagicMock()
            added: list[str] = []

            def _add(request: Any, request_id: str = "") -> None:
                added.append(request_id)

            def _execute() -> None:
                attempt["n"] += 1
                for rid in added:
                    if rid == "flaky" and attempt["n"] == 1:
                        callback(rid, {}, _http_error(429))
                    else:
                        callback(rid, {"id": rid}, None)

            batch.add = _add
            batch.execute = _execute
            return batch

        svc.new_batch_http_request = _new_batch

        result = _run_batch(svc, ["stable", "flaky"], MagicMock())

        assert result == {"stable": {"id": "stable"}, "flaky": {"id": "flaky"}}

    def test_persistent_retryable_error_is_eventually_dropped(self) -> None:
        responses: dict[str, dict[str, Any] | Exception] = {
            "doomed": _http_error(503),
        }
        result = _run_batch(_make_svc(responses), ["doomed"], MagicMock())
        assert "doomed" not in result

    def test_chunks_into_batches_of_100(self) -> None:
        ids = [f"m{i}" for i in range(250)]
        responses = {mid: {"id": mid} for mid in ids}
        batch_sizes: list[int] = []

        result = _run_batch(_make_svc(responses, batch_sizes), ids, MagicMock())

        assert len(result) == 250
        assert batch_sizes == [100, 100, 50]

    def test_empty_input(self) -> None:
        svc = MagicMock()
        assert _run_batch(svc, [], MagicMock()) == {}
        svc.new_batch_http_request.assert_not_called()
