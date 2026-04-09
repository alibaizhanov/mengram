"""Unit tests for CloudMemory Python SDK client."""

import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from cloud.client import CloudMemory, QuotaExceededError


@pytest.fixture
def client():
    """Create a CloudMemory client with a test API key."""
    return CloudMemory(api_key="om-test-key-123")


@pytest.fixture
def client_custom_url():
    """Create a CloudMemory client with a custom base URL."""
    return CloudMemory(api_key="om-test-key-123", base_url="https://custom.api.io/")


def _mock_response(body: dict, status: int = 200, headers: dict = None):
    """Create a mock urllib response."""
    response = MagicMock()
    response.read.return_value = json.dumps(body).encode()
    response.status = status
    response.headers = headers or {}
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    return response


def _mock_http_error(code: int, body: dict = None):
    """Create a mock HTTPError."""
    body_str = json.dumps(body or {"detail": "error"}).encode()
    return urllib.error.HTTPError(
        url="https://mengram.io/v1/test",
        code=code,
        msg="Error",
        hdrs={},
        fp=BytesIO(body_str),
    )


# --- Initialization ---


class TestClientInit:
    """Tests for CloudMemory initialization."""

    def test_default_base_url(self, client):
        assert client.base_url == "https://mengram.io"

    def test_custom_base_url(self, client_custom_url):
        assert client_custom_url.base_url == "https://custom.api.io"

    def test_base_url_trailing_slash_stripped(self):
        c = CloudMemory(api_key="key", base_url="https://example.com/")
        assert c.base_url == "https://example.com"

    def test_api_key_stored(self, client):
        assert client.api_key == "om-test-key-123"


# --- Search ---


class TestSearch:
    """Tests for CloudMemory.search() method."""

    @patch("urllib.request.urlopen")
    def test_search_returns_results(self, mock_urlopen, client):
        results = [{"entity": "PostgreSQL", "type": "Technology", "score": 0.95, "facts": ["Uses pool-size=20"]}]
        mock_urlopen.return_value = _mock_response({"results": results})

        response = client.search("database issues", user_id="user-1")

        assert response == results

    @patch("urllib.request.urlopen")
    def test_search_sends_correct_body(self, mock_urlopen, client):
        mock_urlopen.return_value = _mock_response({"results": []})

        client.search("database", user_id="user-1", limit=10, graph_depth=3)

        call_args = mock_urlopen.call_args[0][0]
        body = json.loads(call_args.data)
        assert body["query"] == "database"
        assert body["user_id"] == "user-1"
        assert body["limit"] == 10
        assert body["graph_depth"] == 3

    @patch("urllib.request.urlopen")
    def test_search_sends_correct_url_and_method(self, mock_urlopen, client):
        mock_urlopen.return_value = _mock_response({"results": []})

        client.search("test query")

        call_args = mock_urlopen.call_args[0][0]
        assert call_args.full_url == "https://mengram.io/v1/search"
        assert call_args.method == "POST"

    @patch("urllib.request.urlopen")
    def test_search_sends_auth_header(self, mock_urlopen, client):
        mock_urlopen.return_value = _mock_response({"results": []})

        client.search("test")

        call_args = mock_urlopen.call_args[0][0]
        assert call_args.headers["Authorization"] == "Bearer om-test-key-123"
        assert call_args.headers["Content-type"] == "application/json"

    @patch("urllib.request.urlopen")
    def test_search_default_params(self, mock_urlopen, client):
        mock_urlopen.return_value = _mock_response({"results": []})

        client.search("test")

        body = json.loads(mock_urlopen.call_args[0][0].data)
        assert body["user_id"] == "default"
        assert body["limit"] == 5
        assert body["graph_depth"] == 2

    @patch("urllib.request.urlopen")
    def test_search_optional_filters(self, mock_urlopen, client):
        mock_urlopen.return_value = _mock_response({"results": []})

        client.search("test", agent_id="bot-1", run_id="run-1", app_id="app-1",
                       filters={"source": "slack"})

        body = json.loads(mock_urlopen.call_args[0][0].data)
        assert body["agent_id"] == "bot-1"
        assert body["run_id"] == "run-1"
        assert body["app_id"] == "app-1"
        assert body["filters"] == {"source": "slack"}

    @patch("urllib.request.urlopen")
    def test_search_optional_params_excluded_when_none(self, mock_urlopen, client):
        mock_urlopen.return_value = _mock_response({"results": []})

        client.search("test")

        body = json.loads(mock_urlopen.call_args[0][0].data)
        assert "agent_id" not in body
        assert "run_id" not in body
        assert "app_id" not in body
        assert "filters" not in body

    @patch("urllib.request.urlopen")
    def test_search_empty_results(self, mock_urlopen, client):
        mock_urlopen.return_value = _mock_response({"results": []})

        results = client.search("nonexistent")

        assert results == []

    @patch("urllib.request.urlopen")
    def test_search_missing_results_key(self, mock_urlopen, client):
        mock_urlopen.return_value = _mock_response({"status": "ok"})

        results = client.search("test")

        assert results == []


# --- Add ---


class TestAdd:
    """Tests for CloudMemory.add() method."""

    @patch("urllib.request.urlopen")
    def test_add_sends_messages(self, mock_urlopen, client):
        expected_response = {"status": "accepted", "job_id": "job-123"}
        mock_urlopen.return_value = _mock_response(expected_response)

        messages = [
            {"role": "user", "content": "Fixed the OOM bug"},
            {"role": "assistant", "content": "Noted the fix"},
        ]
        result = client.add(messages, user_id="user-1")

        body = json.loads(mock_urlopen.call_args[0][0].data)
        assert body["messages"] == messages
        assert body["user_id"] == "user-1"
        assert result == expected_response

    @patch("urllib.request.urlopen")
    def test_add_correct_endpoint(self, mock_urlopen, client):
        mock_urlopen.return_value = _mock_response({"status": "accepted"})

        client.add([{"role": "user", "content": "test"}])

        call_args = mock_urlopen.call_args[0][0]
        assert call_args.full_url == "https://mengram.io/v1/add"
        assert call_args.method == "POST"

    @patch("urllib.request.urlopen")
    def test_add_optional_params(self, mock_urlopen, client):
        mock_urlopen.return_value = _mock_response({"status": "accepted"})

        client.add(
            [{"role": "user", "content": "test"}],
            agent_id="agent-1",
            run_id="run-1",
            app_id="app-1",
            source="slack",
            metadata={"channel": "#dev"},
            agent_mode=True,
        )

        body = json.loads(mock_urlopen.call_args[0][0].data)
        assert body["agent_id"] == "agent-1"
        assert body["run_id"] == "run-1"
        assert body["app_id"] == "app-1"
        assert body["source"] == "slack"
        assert body["metadata"] == {"channel": "#dev"}
        assert body["agent_mode"] is True

    @patch("urllib.request.urlopen")
    def test_add_optional_params_excluded_when_none(self, mock_urlopen, client):
        mock_urlopen.return_value = _mock_response({"status": "accepted"})

        client.add([{"role": "user", "content": "test"}])

        body = json.loads(mock_urlopen.call_args[0][0].data)
        assert "agent_id" not in body
        assert "run_id" not in body
        assert "app_id" not in body
        assert "source" not in body
        assert "metadata" not in body
        assert "agent_mode" not in body

    @patch("urllib.request.urlopen")
    def test_add_default_user_id(self, mock_urlopen, client):
        mock_urlopen.return_value = _mock_response({"status": "accepted"})

        client.add([{"role": "user", "content": "test"}])

        body = json.loads(mock_urlopen.call_args[0][0].data)
        assert body["user_id"] == "default"


# --- Quota ---


class TestQuota:
    """Tests for CloudMemory.quota property."""

    @patch("urllib.request.urlopen")
    def test_quota_parses_headers(self, mock_urlopen, client):
        headers = {
            "X-Quota-Add-Used": "5",
            "X-Quota-Add-Limit": "30",
            "X-Quota-Search-Used": "12",
            "X-Quota-Search-Limit": "100",
        }
        mock_urlopen.return_value = _mock_response({"results": []}, headers=headers)

        client.search("trigger headers")

        assert client.quota == {
            "add": {"used": 5, "limit": 30},
            "search": {"used": 12, "limit": 100},
        }

    @patch("urllib.request.urlopen")
    def test_quota_partial_headers(self, mock_urlopen, client):
        headers = {
            "X-Quota-Search-Used": "3",
            "X-Quota-Search-Limit": "50",
        }
        mock_urlopen.return_value = _mock_response({"results": []}, headers=headers)

        client.search("test")

        assert client.quota == {"search": {"used": 3, "limit": 50}}
        assert "add" not in client.quota

    def test_quota_no_prior_request(self, client):
        assert client.quota == {}

    @patch("urllib.request.urlopen")
    def test_quota_updates_after_each_request(self, mock_urlopen, client):
        headers_1 = {"X-Quota-Search-Used": "1", "X-Quota-Search-Limit": "100"}
        headers_2 = {"X-Quota-Search-Used": "2", "X-Quota-Search-Limit": "100"}

        mock_urlopen.return_value = _mock_response({"results": []}, headers=headers_1)
        client.search("first")
        assert client.quota["search"]["used"] == 1

        mock_urlopen.return_value = _mock_response({"results": []}, headers=headers_2)
        client.search("second")
        assert client.quota["search"]["used"] == 2


# --- Error Handling ---


class TestErrorHandling:
    """Tests for error handling in CloudMemory."""

    @patch("urllib.request.urlopen")
    def test_quota_exceeded_error(self, mock_urlopen, client):
        detail = {"action": "add", "limit": 30, "used": 30, "plan": "free"}
        mock_urlopen.side_effect = _mock_http_error(402, {"detail": detail})

        with pytest.raises(QuotaExceededError) as exc_info:
            client.add([{"role": "user", "content": "test"}])

        assert exc_info.value.action == "add"
        assert exc_info.value.limit == 30
        assert exc_info.value.current == 30
        assert exc_info.value.plan == "free"
        assert "Quota exceeded" in str(exc_info.value)

    @patch("urllib.request.urlopen")
    def test_http_error_raises_exception(self, mock_urlopen, client):
        mock_urlopen.side_effect = _mock_http_error(500, {"detail": "Internal error"})

        with pytest.raises(Exception, match="API error 500"):
            client.search("test")

    @patch("urllib.request.urlopen")
    def test_404_error(self, mock_urlopen, client):
        mock_urlopen.side_effect = _mock_http_error(404, {"detail": "Not found"})

        with pytest.raises(Exception, match="API error 404"):
            client.search("test")

    @patch("urllib.request.urlopen")
    def test_network_error_after_retries(self, mock_urlopen, client):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with pytest.raises(Exception, match="Network error"):
            client.search("test")


# --- Retry Logic ---


class TestRetryLogic:
    """Tests for retry behavior on transient errors."""

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_retry_on_429(self, mock_urlopen, mock_sleep, client):
        mock_urlopen.side_effect = [
            _mock_http_error(429, {"detail": "Rate limited"}),
            _mock_response({"results": []}),
        ]

        results = client.search("test")

        assert results == []
        assert mock_urlopen.call_count == 2

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_retry_on_502(self, mock_urlopen, mock_sleep, client):
        mock_urlopen.side_effect = [
            _mock_http_error(502, {"detail": "Bad gateway"}),
            _mock_response({"results": []}),
        ]

        results = client.search("test")

        assert results == []
        assert mock_urlopen.call_count == 2

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_retry_on_503(self, mock_urlopen, mock_sleep, client):
        mock_urlopen.side_effect = [
            _mock_http_error(503, {"detail": "Service unavailable"}),
            _mock_response({"results": []}),
        ]

        results = client.search("test")

        assert results == []
        assert mock_urlopen.call_count == 2

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_retry_on_504(self, mock_urlopen, mock_sleep, client):
        mock_urlopen.side_effect = [
            _mock_http_error(504, {"detail": "Gateway timeout"}),
            _mock_response({"results": []}),
        ]

        results = client.search("test")

        assert results == []
        assert mock_urlopen.call_count == 2

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_no_retry_on_400(self, mock_urlopen, mock_sleep, client):
        mock_urlopen.side_effect = _mock_http_error(400, {"detail": "Bad request"})

        with pytest.raises(Exception, match="API error 400"):
            client.search("test")

        assert mock_urlopen.call_count == 1

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_max_3_attempts(self, mock_urlopen, mock_sleep, client):
        mock_urlopen.side_effect = [
            _mock_http_error(503, {"detail": "down"}),
            _mock_http_error(503, {"detail": "down"}),
            _mock_http_error(503, {"detail": "down"}),
        ]

        with pytest.raises(Exception, match="API error 503"):
            client.search("test")

        assert mock_urlopen.call_count == 3

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_retry_network_error(self, mock_urlopen, mock_sleep, client):
        mock_urlopen.side_effect = [
            urllib.error.URLError("Connection refused"),
            _mock_response({"results": []}),
        ]

        results = client.search("test")

        assert results == []
        assert mock_urlopen.call_count == 2


# --- QuotaExceededError ---


class TestQuotaExceededError:
    """Tests for QuotaExceededError exception class."""

    def test_attributes(self):
        detail = {"action": "search", "limit": 100, "used": 100, "plan": "pro"}
        err = QuotaExceededError(detail)

        assert err.action == "search"
        assert err.limit == 100
        assert err.current == 100
        assert err.plan == "pro"

    def test_message_format(self):
        detail = {"action": "add", "limit": 30, "used": 30, "plan": "free"}
        err = QuotaExceededError(detail)

        assert "Quota exceeded for 'add'" in str(err)
        assert "30/30" in str(err)
        assert "plan: free" in str(err)
        assert "mengram.io/dashboard" in str(err)

    def test_defaults_for_missing_keys(self):
        err = QuotaExceededError({})

        assert err.action == "unknown"
        assert err.limit == 0
        assert err.current == 0
        assert err.plan == "free"
