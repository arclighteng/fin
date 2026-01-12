import base64
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx

from .config import Config


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

    def fetch_accounts(self) -> Dict[str, Any]:
        """
        Unfiltered accounts payload (no date range).
        """
        base, _ = self._split_access_url()
        url = f"{base}/accounts"
        r = self._client.get(url, headers=self._headers())
        r.raise_for_status()
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

        r = self._client.get(url, headers=self._headers(), params=params)
        r.raise_for_status()
        return r.json()

    def fetch_account_set_range(self, start_date: date, end_date_exclusive: date) -> Dict[str, Any]:
        """
        SimpleFIN Bridge limits /accounts date range to 60 days per request.
        This method chunks and merges results.
        """
        merged: Dict[str, Any] = {"errors": [], "accounts": []}
        by_id: Dict[str, Dict[str, Any]] = {}
        errors_seen = set()

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

    def close(self) -> None:
        self._client.close()
