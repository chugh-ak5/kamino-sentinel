"""Tests for the resilient Solana RPC client."""

from unittest.mock import MagicMock

import pytest
import requests

from kamino_sentinel.rpc import (
    RpcEndpointError,
    RpcError,
    RpcExhaustedError,
    RpcTimeoutError,
    SolanaRpcClient,
)


def _resp(status_code=200, json_body=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_body if json_body is not None else {"result": "ok"}
    return r


@pytest.fixture
def client():
    c = SolanaRpcClient(
        endpoints=["https://a.example", "https://b.example"],
        timeout=1.0,
        total_budget=5.0,
        max_retries=1,
    )
    c.session = MagicMock()
    return c


# --- happy path ------------------------------------------------------------

def test_successful_call_returns_result(client):
    client.session.post.return_value = _resp(200, {"result": {"slot": 42}})
    assert client.call("getSlot") == {"slot": 42}


def test_successful_call_uses_current_endpoint(client):
    client.session.post.return_value = _resp(200, {"result": 1})
    client.call("getSlot")
    assert client.session.post.call_args[0][0] == "https://a.example"


def test_payload_shape_is_jsonrpc_2(client):
    client.session.post.return_value = _resp(200, {"result": 1})
    client.call("getAccountInfo", ["abc"])
    payload = client.session.post.call_args[1]["json"]
    assert payload["jsonrpc"] == "2.0"
    assert payload["method"] == "getAccountInfo"
    assert payload["params"] == ["abc"]


# --- failover --------------------------------------------------------------

def test_failover_to_second_endpoint_on_retryable_status(client):
    client.session.post.side_effect = [
        _resp(503),
        _resp(200, {"result": "recovered"}),
    ]
    assert client.call("getSlot") == "recovered"
    assert client.current_endpoint == "https://b.example"


def test_rotation_wraps_around(client):
    client.session.post.side_effect = [
        _resp(429),
        _resp(429),
        _resp(200, {"result": "ok"}),
    ]
    client.call("getSlot")
    assert client.current_endpoint == "https://a.example"


def test_all_endpoints_failing_raises_exhausted(client):
    client.session.post.return_value = _resp(503)
    with pytest.raises(RpcError):
        client.call("getSlot")


# --- error classification --------------------------------------------------

def test_jsonrpc_error_is_not_retried(client):
    client.session.post.return_value = _resp(200, {"error": {"code": -32602}})
    with pytest.raises(RpcError):
        client.call("badMethod")
    assert client.session.post.call_count == 1


def test_non_retryable_http_status_raises(client):
    client.session.post.return_value = _resp(403)
    with pytest.raises(RpcError):
        client.call("getSlot")


def test_timeout_is_retried_then_succeeds(client):
    client.session.post.side_effect = [
        requests.Timeout("slow"),
        _resp(200, {"result": "ok"}),
    ]
    assert client.call("getSlot") == "ok"


def test_connection_error_is_retried(client):
    client.session.post.side_effect = [
        requests.ConnectionError("refused"),
        _resp(200, {"result": "ok"}),
    ]
    assert client.call("getSlot") == "ok"


def test_persistent_timeout_raises_rpc_error(client):
    client.session.post.side_effect = requests.Timeout("always slow")
    with pytest.raises(RpcError):
        client.call("getSlot")


# --- budget ----------------------------------------------------------------

def test_total_budget_exceeded_raises(client):
    client.total_budget = 0.0
    client.session.post.return_value = _resp(200, {"result": 1})
    with pytest.raises(RpcError):
        client.call("getSlot")


# --- construction ----------------------------------------------------------

def test_defaults_come_from_config():
    c = SolanaRpcClient()
    assert c.endpoints
    assert c.timeout > 0
    assert c.total_budget > 0


def test_explicit_endpoints_override_defaults():
    c = SolanaRpcClient(endpoints=["https://only.example"])
    assert c.endpoints == ["https://only.example"]
    assert c.current_endpoint == "https://only.example"
