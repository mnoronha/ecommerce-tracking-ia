"""
DataForSEO API client — sync, used for AI Visibility automatic collection.

Endpoints:
  POST /v3/ai_optimization/llm_mentions/live/advanced — brand mentions in LLM responses
  POST /v2/account_data/balance/live                  — credit balance check
"""

import base64
import logging
from typing import Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.dataforseo.com"

# Pricing (USD per unit) — DataForSEO price list as of 2026
COST_PER_LLM_MENTION = 0.001   # per keyword × LLM platform


class DataForSEOError(Exception):
    def __init__(self, msg: str, status_code: Optional[int] = None):
        super().__init__(msg)
        self.api_status_code = status_code


class DataForSEOClient:
    def __init__(self):
        self._login    = settings.DATAFORSEO_LOGIN
        self._password = settings.DATAFORSEO_PASSWORD
        if not self._login or not self._password:
            raise DataForSEOError("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not configured in env")

    def _auth_header(self) -> dict:
        token = base64.b64encode(f"{self._login}:{self._password}".encode()).decode()
        return {
            "Authorization": f"Basic {token}",
            "Content-Type":  "application/json",
        }

    def _post(self, path: str, payload: list) -> dict:
        url = f"{_BASE_URL}{path}"
        try:
            resp = httpx.post(url, json=payload, headers=self._auth_header(), timeout=90.0)
        except httpx.TimeoutException:
            raise DataForSEOError(f"Timeout calling DataForSEO {path}")
        except httpx.RequestError as exc:
            raise DataForSEOError(f"Network error calling {path}: {exc}")

        if resp.status_code != 200:
            raise DataForSEOError(
                f"HTTP {resp.status_code} from DataForSEO {path}: {resp.text[:300]}",
                status_code=resp.status_code,
            )

        data = resp.json()
        api_status = data.get("status_code")
        # 20000 = OK, 20100 = task_created (async), both are success
        if api_status not in (20000, 20100):
            msg = data.get("status_message") or f"API error {api_status}"
            raise DataForSEOError(msg, status_code=api_status)

        return data

    # ── Public API ────────────────────────────────────────────────────────────

    def get_llm_mentions(
        self,
        keywords:      list[str],
        target_domain: str,
        location_code: int = 2076,
        language_code: str = "pt",
        llms:          Optional[list[str]] = None,
    ) -> dict:
        """
        Query LLM Mentions API for each keyword across the specified LLM platforms.

        Returns raw DataForSEO response. Each task.result entry has:
          keyword, platform, items[{mentioned, position, sentiment, context, cited_sources, ...}]
        """
        if not keywords:
            return {"tasks": [], "status_code": 20000}

        llms = llms or ["chatgpt", "gemini", "perplexity"]
        clean_domain = (
            target_domain
            .removeprefix("https://")
            .removeprefix("http://")
            .rstrip("/")
        )

        # Batch keywords; DataForSEO allows max 100 tasks per request
        tasks = [
            {
                "keyword":       kw,
                "target_type":   "domain",
                "target_domain": clean_domain,
                "location_code": location_code,
                "language_code": language_code,
                "platforms":     llms,
            }
            for kw in keywords[:100]
        ]

        return self._post("/v3/ai_optimization/llm_mentions/live/advanced", tasks)

    def get_account_balance(self) -> dict:
        """Returns {"balance": float, "bonus": float} in DataForSEO credits."""
        data  = self._post("/v2/account_data/balance/live", [{}])
        tasks = data.get("tasks") or [{}]
        result = ((tasks[0] or {}).get("result") or [{}])[0] or {}
        return {
            "balance": result.get("balance", 0),
            "bonus":   result.get("bonus_balance", 0),
        }

    # ── Static helpers ────────────────────────────────────────────────────────

    @staticmethod
    def estimate_cost(num_keywords: int, num_llms: int) -> float:
        """Estimate USD cost for one collection run (no network call)."""
        return num_keywords * num_llms * COST_PER_LLM_MENTION
