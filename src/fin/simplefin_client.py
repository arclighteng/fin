import base64
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict
from urllib.parse import urlsplit

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from .config import Config

log = logging.getLogger(__name__)


def claim_access_url(setup_token: str) -> str:
    """
    Exchange a SimpleFIN Setup Token for a permanent Access URL.

    SimpleFIN authentication is a two-step process:
    1. User gets a Setup Token from SimpleFIN Bridge (base64-encoded claim URL)
    2. App POSTs to the claim URL to receive the permanent Access URL

    Args:
        setup_token: Base64-encoded setup token from SimpleFIN Bridge

    Returns:
        The permanent Access URL (https://user:pass@server/simplefin)

    Raises:
        ValueError: If the token is invalid or already claimed
        httpx.HTTPStatusError: If the claim request fails
    """
    # Decode the base64 setup token to get the claim URL
    try:
        claim_url = base64.b64decode(setup_token).decode("utf-8")
    except Exception as e:
        raise ValueError(f"Invalid setup token - must be base64 encoded: {e}")

    if not claim_url.startswith("https://"):
        raise ValueError("Invalid setup token - claim URL must use HTTPS")

    # Only log the hostname, never credentials
    try:
        from urllib.parse import urlsplit
        claim_host = urlsplit(claim_url).hostname or "unknown"
    except Exception:
        claim_host = "unknown"
    log.info(f"Claiming access URL from: {claim_host}")

    # POST to the claim URL (empty body)
    with httpx.Client(timeout=30.0) as client:
        response = client.post(claim_url)

        if response.status_code == 403:
            raise ValueError("Setup token has already been claimed. Generate a new one from SimpleFIN Bridge.")

        response.raise_for_status()

        access_url = response.text.strip()

        # Validate the access URL looks correct (don't leak content in error)
        if not access_url.startswith("https://") or "@" not in access_url:
            raise ValueError("Unexpected response from claim endpoint - expected HTTPS URL with credentials")

        return access_url


class SimpleFinClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._client = httpx.Client(timeout=30.0)

    def _split_access_url(self) -> tuple[str, str]:
        """
        Returns:
          - base_url_without_creds (e.g. https://server/path)
          - Authorization header value (e.g. Basic abc123==)
        """
        u = urlsplit(self.cfg.simplefin_access_url)
        if not u.scheme or not u.hostname:
            raise ValueError("SIMPLEFIN_ACCESS_URL is not a valid URL")
        if u.scheme != "https":
            raise ValueError("SIMPLEFIN_ACCESS_URL must use HTTPS (credentials would be sent in plaintext over HTTP)")
        if not u.username or not u.password:
            raise ValueError("SIMPLEFIN_ACCESS_URL must include embedded credentials (userinfo)")

        userinfo = f"{u.username}:{u.password}"
        b64 = base64.b64encode(userinfo.encode("ascii")).decode("ascii")

        port = f":{u.port}" if u.port else ""
        path = u.path.rstrip("/")
        base_no_creds = f"{u.scheme}://{u.hostname}{port}{path}"

        return base_no_creds, f"Basic {b64}"

    def _headers(self) -> dict:
        _, auth = self._split_access_url()
        return {"Authorization": auth}

    @staticmethod
    def _to_epoch_seconds_start(d: date) -> int:
        # start_date inclusive at 00:00 UTC
        dt = datetime.combine(d, time.min, tzinfo=timezone.utc)
        return int(dt.timestamp())

    @staticmethod
    def _to_epoch_seconds_end_exclusive(d: date) -> int:
        # end_date exclusive at 00:00 UTC for that date
        dt = datetime.combine(d, time.min, tzinfo=timezone.utc)
        return int(dt.timestamp())

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
    def _get_with_retry(self, url: str, params: dict | None = None) -> httpx.Response:
        """
        Execute GET request with retry on transient failures.
        
        Retries on:
          - TimeoutException: Network timeout
          - ConnectError: Connection refused, DNS failure, etc.
        
        Does NOT retry on:
          - HTTPStatusError: Let caller handle 4xx/5xx
        """
        r = self._client.get(url, headers=self._headers(), params=params)
        r.raise_for_status()
        return r

    def fetch_accounts(self) -> Dict[str, Any]:
        """
        Unfiltered accounts payload (no date range).
        """
        base, _ = self._split_access_url()
        url = f"{base}/accounts"
        r = self._get_with_retry(url)
        return r.json()

    def fetch_account_set(self, start_date: date, end_date_exclusive: date) -> Dict[str, Any]:
        """
        GET /accounts with start_date/end_date (epoch seconds).
        end_date is exclusive.
        """
        base, _ = self._split_access_url()
        url = f"{base}/accounts"
        params = {
            "start-date": self._to_epoch_seconds_start(start_date),
            "end-date": self._to_epoch_seconds_end_exclusive(end_date_exclusive),
        }

        r = self._get_with_retry(url, params=params)
        return r.json()

    def fetch_account_set_range(self, start_date: date, end_date_exclusive: date) -> Dict[str, Any]:
        """
        SimpleFIN Bridge limits /accounts date range to 60 days per request.
        This method chunks and merges results.
        """
        merged: Dict[str, Any] = {"errors": [], "accounts": []}
        by_id: Dict[str, Dict[str, Any]] = {}
        errors_seen: set[str] = set()

        cursor = start_date
        while cursor < end_date_exclusive:
            window_end = min(cursor + timedelta(days=60), end_date_exclusive)
            chunk = self.fetch_account_set(cursor, window_end)

            for e in chunk.get("errors", []) or []:
                es = str(e)
                if es not in errors_seen:
                    errors_seen.add(es)
                    merged["errors"].append(e)

            for acct in chunk.get("accounts", []) or []:
                aid = str(acct.get("id"))
                if aid not in by_id:
                    by_id[aid] = acct
                else:
                    existing = by_id[aid]
                    tx_existing = existing.get("transactions") or []
                    tx_new = acct.get("transactions") or []
                    if tx_new:
                        existing["transactions"] = tx_existing + tx_new

                    for k, v in acct.items():
                        if k != "transactions":
                            existing[k] = v

            cursor = window_end

        merged["accounts"] = list(by_id.values())
        return merged

    def fetch_all_available(self) -> Dict[str, Any]:
        """
        Fetch ALL available transaction data from SimpleFIN.

        SimpleFIN/banks typically provide 90-180 days of history.
        We request 2 years to ensure we get everything available.
        The bank will only return what they have - no harm in asking for more.

        This is the preferred method for sync - simple and complete.
        """
        # Request 2 years back - banks will return whatever they have
        start_date = date.today() - timedelta(days=730)
        end_date_exclusive = date.today() + timedelta(days=1)
        return self.fetch_account_set_range(start_date, end_date_exclusive)

    def close(self) -> None:
        self._client.close()