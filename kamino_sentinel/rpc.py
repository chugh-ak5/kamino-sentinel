"""
Solana JSON-RPC and Price Data Client with automatic multi-endpoint failover.
"""

import logging
import time
from typing import Any, Dict, List, Optional
import requests

from kamino_sentinel.config import (
    DEFAULT_RPC_ENDPOINTS,
    RPC_BACKOFF_BASE_SECONDS,
    RPC_BACKOFF_MAX_SECONDS,
    RPC_MAX_RETRIES,
    RPC_TIMEOUT_SECONDS,
    RPC_TOTAL_BUDGET_SECONDS,
)

logger = logging.getLogger(__name__)


class RpcError(RuntimeError):
    """Base class for all Solana RPC failures."""


class RpcTimeoutError(RpcError):
    """A single RPC request exceeded its per-request timeout."""


class RpcProtocolError(RpcError):
    """Deterministic JSON-RPC protocol error (bad method/params). Never retried."""


class RpcEndpointError(RpcError):
    """An endpoint returned a retryable HTTP/JSON-RPC error."""


class RpcExhaustedError(RpcError):
    """Every configured endpoint failed within the retry budget."""


class SolanaRpcClient:
    def __init__(
        self,
        endpoints: Optional[List[str]] = None,
        timeout: Optional[float] = None,
        total_budget: Optional[float] = None,
        max_retries: Optional[int] = None,
    ):
        self.endpoints = endpoints or list(DEFAULT_RPC_ENDPOINTS)
        self.current_endpoint_idx = 0
        self.timeout = RPC_TIMEOUT_SECONDS if timeout is None else timeout
        self.total_budget = RPC_TOTAL_BUDGET_SECONDS if total_budget is None else total_budget
        self.max_retries = RPC_MAX_RETRIES if max_retries is None else max_retries
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    @property
    def current_endpoint(self) -> str:
        return self.endpoints[self.current_endpoint_idx]

    def _rotate_endpoint(self) -> None:
        self.current_endpoint_idx = (self.current_endpoint_idx + 1) % len(self.endpoints)
        logger.warning("Switched Solana RPC endpoint to: %s", self.current_endpoint)

    def call(self, method: str, params: Optional[List[Any]] = None) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000) % 1000000,
            "method": method,
            "params": params or [],
        }

        start_time = time.monotonic()
        attempts = 0
        max_attempts = max(1, len(self.endpoints) * (self.max_retries + 1))
        last_error = None
        backoff = RPC_BACKOFF_BASE_SECONDS

        while attempts < max_attempts:
            if (time.monotonic() - start_time) > self.total_budget:
                raise RpcTimeoutError(
                    f"RPC call '{method}' exceeded total budget of {self.total_budget}s. Last error: {last_error}"
                )

            attempts += 1
            endpoint = self.current_endpoint
            try:
                # Cap request timeout by remaining budget
                remaining_budget = max(0.5, self.total_budget - (time.monotonic() - start_time))
                actual_timeout = min(self.timeout, remaining_budget)

                resp = self.session.post(endpoint, json=payload, timeout=actual_timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    if "error" in data:
                        raise RpcProtocolError(f"RPC error from {endpoint}: {data['error']}")
                    return data.get("result")
                elif resp.status_code in (429, 503, 504):
                    logger.warning("RPC %s responded with status %d (rate-limited/unavailable). Rotating.", endpoint, resp.status_code)
                    self._rotate_endpoint()
                    time.sleep(min(backoff, RPC_BACKOFF_MAX_SECONDS))
                    backoff *= 1.5
                    continue
                else:
                    last_error = RpcEndpointError(f"HTTP {resp.status_code}: {resp.text[:120]}")
                    self._rotate_endpoint()
            except RpcProtocolError:
                # Deterministic protocol error: retrying cannot help.
                raise
            except Exception as e:
                last_error = e
                logger.debug("RPC %s call failed: %s", endpoint, e)
                self._rotate_endpoint()
                time.sleep(min(backoff, RPC_BACKOFF_MAX_SECONDS))
                backoff *= 1.5

        raise RpcExhaustedError(f"All Solana RPC endpoints exhausted ({attempts} attempts). Last error: {last_error}")

    def get_account_info(self, pubkey: str, encoding: str = "base64") -> Optional[Dict[str, Any]]:
        result = self.call("getAccountInfo", [pubkey, {"encoding": encoding}])
        if result and "value" in result:
            return result["value"]
        return None

    def get_multiple_accounts(self, pubkeys: List[str], encoding: str = "base64") -> List[Optional[Dict[str, Any]]]:
        if not pubkeys:
            return []
        result = self.call("getMultipleAccounts", [pubkeys, {"encoding": encoding}])
        if result and "value" in result:
            return result["value"]
        return []

    def get_program_accounts(
        self,
        program_id: str,
        filters: Optional[List[Dict[str, Any]]] = None,
        encoding: str = "base64",
        data_slice: Optional[Dict[str, int]] = None
    ) -> List[Dict[str, Any]]:
        config: Dict[str, Any] = {"encoding": encoding}
        if filters:
            config["filters"] = filters
        if data_slice:
            config["dataSlice"] = data_slice
        result = self.call("getProgramAccounts", [program_id, config])
        return result or []


class PriceOracleClient:
    """Fetches real-time market prices for Solana SPL tokens."""

    JUPITER_PRICE_V2_URL = "https://api.jup.ag/price/v2"

    def __init__(self, timeout: float = 6.0):
        self.session = requests.Session()
        self.timeout = timeout
        # Fallback cached prices in case price feeds are temporarily throttled
        self._price_cache: Dict[str, float] = {
            "So11111111111111111111111111111111111111112": 150.0,  # SOL
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": 1.0,    # USDC
            "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": 1.0,    # USDT
            "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": 175.0,  # JitoSOL
            "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": 180.0,  # mSOL
            "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1": 178.0,  # bSOL
            "27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4": 3.2,    # JLP
        }

    def get_prices(self, mint_addresses: List[str]) -> Dict[str, float]:
        if not mint_addresses:
            return {}

        results: Dict[str, float] = {}
        try:
            ids = ",".join(mint_addresses)
            resp = self.session.get(
                self.JUPITER_PRICE_V2_URL,
                params={"ids": ids},
                timeout=self.timeout
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                for mint, item in data.items():
                    if item and "price" in item:
                        p = float(item["price"])
                        results[mint] = p
                        self._price_cache[mint] = p
        except Exception:
            pass

        # Fill any missing with cached or default values
        for m in mint_addresses:
            if m not in results:
                results[m] = self._price_cache.get(m, 1.0)

        return results
