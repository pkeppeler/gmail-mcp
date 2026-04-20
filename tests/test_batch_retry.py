"""Unit tests for the batch retry logic in gmail_client."""

from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]
from httplib2 import Response  # type: ignore[import-untyped]

from gmail_mcp.gmail_client import (
    _execute_with_transport_retry,
    _is_retryable,
    _run_batch,
)


# ---------------------------------------------------------------------------
# Helpers for building mock HttpError objects
# ---------------------------------------------------------------------------


def _make_http_error(status: int) -> HttpError:
    """Create an HttpError with the given status code."""
    resp = Response({"status": str(status)})
    return HttpError(resp, b"error", uri="https://example.com")


# ---------------------------------------------------------------------------
# _is_retryable
# ---------------------------------------------------------------------------


class TestIsRetryable:
    def test_429_is_retryable(self) -> None:
        assert _is_retryable(_make_http_error(429)) is True

    def test_500_is_retryable(self) -> None:
        assert _is_retryable(_make_http_error(500)) is True

    def test_502_is_retryable(self) -> None:
        assert _is_retryable(_make_http_error(502)) is True

    def test_503_is_retryable(self) -> None:
        assert _is_retryable(_make_http_error(503)) is True

    def test_400_not_retryable(self) -> None:
        assert _is_retryable(_make_http_error(400)) is False

    def test_403_not_retryable(self) -> None:
        assert _is_retryable(_make_http_error(403)) is False

    def test_404_not_retryable(self) -> None:
        assert _is_retryable(_make_http_error(404)) is False

    def test_non_http_error_not_retryable(self) -> None:
        assert _is_retryable(RuntimeError("something")) is False

    def test_generic_exception_not_retryable(self) -> None:
        assert _is_retryable(Exception("generic")) is False


# ---------------------------------------------------------------------------
# _execute_with_transport_retry
# ---------------------------------------------------------------------------


class TestExecuteWithTransportRetry:
    @patch("gmail_mcp.gmail_client.time.sleep")
    def test_succeeds_first_try(self, mock_sleep: MagicMock) -> None:
        batch = MagicMock()
        batch.execute.return_value = None

        _execute_with_transport_retry(batch)

        batch.execute.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("gmail_mcp.gmail_client.time.sleep")
    def test_retries_on_transport_failure(self, mock_sleep: MagicMock) -> None:
        batch = MagicMock()
        # Fail twice, succeed third time.
        batch.execute.side_effect = [OSError("DNS failed"), OSError("timeout"), None]

        _execute_with_transport_retry(batch)

        assert batch.execute.call_count == 3
        assert mock_sleep.call_count == 2
        # Exponential backoff: 1.0, 2.0
        mock_sleep.assert_any_call(1.0)
        mock_sleep.assert_any_call(2.0)

    @patch("gmail_mcp.gmail_client.time.sleep")
    def test_raises_after_all_retries_exhausted(self, mock_sleep: MagicMock) -> None:
        batch = MagicMock()
        batch.execute.side_effect = OSError("persistent failure")

        with pytest.raises(OSError, match="persistent failure"):
            _execute_with_transport_retry(batch)

        # 3 attempts (matching _RETRY_DELAYS length)
        assert batch.execute.call_count == 3
        assert mock_sleep.call_count == 2


# ---------------------------------------------------------------------------
# _run_batch
# ---------------------------------------------------------------------------


def _make_mock_svc(responses: dict[str, dict | Exception]) -> MagicMock:
    """Build a mock service whose batch executes a callback per request.

    Args:
        responses: mapping of request_id -> response dict or Exception.
            If the value is an Exception, the callback gets it as the exception arg.
    """
    svc = MagicMock()

    def _new_batch(callback: object = None) -> MagicMock:
        batch = MagicMock()
        added: list[str] = []

        def _add(request: object, request_id: str = "") -> None:
            added.append(request_id)

        def _execute() -> None:
            for rid in added:
                result = responses.get(rid)
                if isinstance(result, Exception):
                    callback(rid, {}, result)  # type: ignore[operator]
                else:
                    callback(rid, result, None)  # type: ignore[operator]

        batch.add = _add
        batch.execute = _execute
        return batch

    svc.new_batch_http_request = _new_batch
    return svc


class TestRunBatch:
    @patch("gmail_mcp.gmail_client.time.sleep")
    def test_all_succeed(self, mock_sleep: MagicMock) -> None:
        responses = {
            "msg1": {"id": "msg1", "data": "hello"},
            "msg2": {"id": "msg2", "data": "world"},
        }
        svc = _make_mock_svc(responses)
        build_request = MagicMock(return_value="fake_request")

        result = _run_batch(svc, ["msg1", "msg2"], build_request)

        assert result == responses
        assert build_request.call_count == 2
        mock_sleep.assert_not_called()

    @patch("gmail_mcp.gmail_client.time.sleep")
    def test_non_retryable_errors_are_dropped(self, mock_sleep: MagicMock) -> None:
        responses: dict[str, dict | Exception] = {
            "msg1": {"id": "msg1", "data": "ok"},
            "msg2": _make_http_error(404),  # not retryable
        }
        svc = _make_mock_svc(responses)

        result = _run_batch(svc, ["msg1", "msg2"], MagicMock(return_value="req"))

        # Only msg1 succeeds; msg2 is dropped without retry.
        assert "msg1" in result
        assert "msg2" not in result
        mock_sleep.assert_not_called()

    @patch("gmail_mcp.gmail_client.time.sleep")
    def test_retryable_errors_are_retried(self, mock_sleep: MagicMock) -> None:
        """A 429 on first attempt succeeds on retry."""
        call_count: dict[str, int] = {"msg1": 0}

        def dynamic_responses() -> dict[str, dict | Exception]:
            """Simulate msg1 failing once then succeeding."""
            call_count["msg1"] += 1
            if call_count["msg1"] == 1:
                return {
                    "msg1": _make_http_error(429),
                    "msg2": {"id": "msg2"},
                }
            return {
                "msg1": {"id": "msg1"},
                "msg2": {"id": "msg2"},
            }

        # Build a service that uses dynamic responses.
        svc = MagicMock()
        batches_executed: list[list[str]] = []

        def _new_batch(callback: object = None) -> MagicMock:
            batch = MagicMock()
            added: list[str] = []

            def _add(request: object, request_id: str = "") -> None:
                added.append(request_id)

            def _execute() -> None:
                batches_executed.append(list(added))
                resps = dynamic_responses()
                for rid in added:
                    r = resps.get(rid)
                    if isinstance(r, Exception):
                        callback(rid, {}, r)  # type: ignore[operator]
                    else:
                        callback(rid, r, None)  # type: ignore[operator]

            batch.add = _add
            batch.execute = _execute
            return batch

        svc.new_batch_http_request = _new_batch

        result = _run_batch(svc, ["msg1", "msg2"], MagicMock(return_value="req"))

        # Both succeed eventually.
        assert "msg1" in result
        assert "msg2" in result
        # First batch had both IDs, second batch only had msg1 (the retry).
        assert batches_executed[0] == ["msg1", "msg2"]
        assert batches_executed[1] == ["msg1"]
        # sleep was called for the backoff.
        assert mock_sleep.call_count >= 1

    @patch("gmail_mcp.gmail_client.time.sleep")
    def test_retryable_error_gives_up_after_max_attempts(self, mock_sleep: MagicMock) -> None:
        """A persistently failing request is eventually dropped."""
        responses: dict[str, dict | Exception] = {
            "msg1": _make_http_error(503),  # always fails
        }
        svc = _make_mock_svc(responses)

        result = _run_batch(svc, ["msg1"], MagicMock(return_value="req"))

        # msg1 never succeeds.
        assert "msg1" not in result
        # Should have retried (initial + 3 retries from _RETRY_DELAYS).
        assert mock_sleep.call_count == 3

    @patch("gmail_mcp.gmail_client.time.sleep")
    def test_chunks_large_lists(self, mock_sleep: MagicMock) -> None:
        """More than 100 IDs are split into multiple batches."""
        ids = [f"msg{i}" for i in range(150)]
        responses = {mid: {"id": mid} for mid in ids}
        svc = _make_mock_svc(responses)

        result = _run_batch(svc, ids, MagicMock(return_value="req"))

        assert len(result) == 150
        mock_sleep.assert_not_called()

    @patch("gmail_mcp.gmail_client.time.sleep")
    def test_empty_list(self, mock_sleep: MagicMock) -> None:
        svc = MagicMock()
        result = _run_batch(svc, [], MagicMock(return_value="req"))

        assert result == {}
        svc.new_batch_http_request.assert_not_called()
