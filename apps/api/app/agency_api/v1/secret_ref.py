"""
Agency API v1 — SecretRef (Etapa 2 design; full 1Password wiring in Etapa 5).

Goals:
- Database stores only ref_id + provider — never a plaintext secret.
- Secrets are resolved at runtime from the configured provider.
- Migration path: clients.{meta_access_token, google_ads_refresh_token, ...}
  → SecretRef rows in core_data_sources.credential_ref (Etapa 5).

Providers:
  ENV          — os.environ[ref_id], for migration compatibility (legacy columns)
  ONEPASSWORD  — 1Password Connect SDK (Etapa 5, not yet wired)
  SUPABASE_VAULT — Supabase Vault (future)

CREDENTIAL_COLUMNS_TO_MIGRATE lists the clients.* columns that will move to SecretRefs.
"""

from __future__ import annotations

import os
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SecretProvider(str, Enum):
    ENV            = "env"             # legacy: os.environ[ref_id]
    ONEPASSWORD    = "1password"       # 1Password Connect (Etapa 5)
    SUPABASE_VAULT = "supabase_vault"  # future


class SecretRefState(str, Enum):
    ACTIVE  = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    UNKNOWN = "UNKNOWN"


class SecretRef(BaseModel):
    """
    Opaque pointer to a secret stored outside the DB.

    Persisted in JSONB columns (e.g. core_data_sources.credential_ref).
    Never contains the plaintext value.
    """

    ref_id: str = Field(
        description="Stable identifier: 1Password item ID, env var name, or vault key"
    )
    provider: SecretProvider
    vault: Optional[str] = Field(
        default=None, description="1Password vault name or UUID"
    )
    item: Optional[str] = Field(
        default=None, description="1Password item title or UUID"
    )
    field_name: Optional[str] = Field(
        default=None,
        alias="field",
        description="1Password field name within the item",
    )
    last_verified_at: Optional[datetime] = None
    state: SecretRefState = SecretRefState.UNKNOWN

    model_config = {"populate_by_name": True}


# ── Resolver ──────────────────────────────────────────────────────────────────


class SecretResolutionError(Exception):
    """Raised when a secret cannot be resolved. Never embed the value in the message."""


def resolve_secret(ref: SecretRef) -> str:
    """
    Resolve a SecretRef to its plaintext value.

    Raises SecretResolutionError if the secret cannot be retrieved.
    The plaintext value is never logged.
    """
    if ref.provider == SecretProvider.ENV:
        value = os.environ.get(ref.ref_id)
        if not value:
            raise SecretResolutionError(
                f"SecretRef env:{ref.ref_id} not found in environment"
            )
        return value

    if ref.provider == SecretProvider.ONEPASSWORD:
        # Etapa 5: wire 1Password Connect SDK.
        # Pre-req: OP_CONNECT_URL + OP_CONNECT_TOKEN set in Railway (never client-side).
        # SDK: pip install onepasswordconnectsdk
        #
        # from onepasswordconnectsdk import new_client
        # from app.config import settings
        # op = new_client(url=settings.OP_CONNECT_URL, token=settings.OP_CONNECT_TOKEN)
        # item = op.get_item(ref.item, vault=ref.vault)
        # field = next(f for f in item.fields if f.label == ref.field_name)
        # return field.value
        from app.config import settings  # noqa: PLC0415 — lazy import to avoid cycle at module init
        if not settings.OP_CONNECT_URL or not settings.OP_CONNECT_TOKEN:
            raise SecretResolutionError(
                "1Password Connect not configured (OP_CONNECT_URL / OP_CONNECT_TOKEN missing). "
                "See docs/etapa-2-report.md §5 for setup instructions."
            )
        raise SecretResolutionError(
            "1Password Connect SDK not yet installed (Etapa 5). "
            f"Run: pip install onepasswordconnectsdk  |  Ref: {ref.vault}/{ref.item}/{ref.field_name}"
        )

    if ref.provider == SecretProvider.SUPABASE_VAULT:
        raise SecretResolutionError("Supabase Vault provider not yet implemented")

    raise SecretResolutionError(f"Unknown provider: {ref.provider}")


# ── Credential column inventory ───────────────────────────────────────────────
# Columns in `clients` that will migrate to SecretRef in core_data_sources.
# Used by the Etapa 5 migration script; list here so the column names are in one place.

CREDENTIAL_COLUMNS_TO_MIGRATE: list[dict[str, str]] = [
    {"column": "meta_access_token",            "label": "meta_api_token"},
    {"column": "google_ads_refresh_token",      "label": "google_ads_refresh_token"},
    {"column": "merchant_center_refresh_token", "label": "merchant_center_refresh_token"},
    {"column": "nuvemshop_access_token",        "label": "nuvemshop_access_token"},
    {"column": "shopify_access_token",          "label": "shopify_access_token"},
    {"column": "woo_consumer_key",              "label": "woo_consumer_key"},
    {"column": "woo_consumer_secret",           "label": "woo_consumer_secret"},
    {"column": "tiktok_access_token",           "label": "tiktok_access_token"},
    {"column": "pinterest_access_token",        "label": "pinterest_access_token"},
    {"column": "ga4_api_secret",                "label": "ga4_api_secret"},
]
