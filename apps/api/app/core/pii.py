"""
Customer PII helpers (LGPD).

The platform identifies customers by hash, never by raw email/phone:

- email_hash  = sha256(lower(trim(email)))          — same as Meta / Google spec
- phone_hash  = sha256(digits only)                  — same as meta_audiences
- email_masked = "ma***@gmail.com"                   — for display only

These hashes are also what ad platforms accept as pre-hashed identifiers,
so audiences and conversions keep working without storing raw values.
The SQL backfill in migration 069 uses the exact same normalization.
"""
from __future__ import annotations

import hashlib
from typing import Optional


def normalize_email(email: Optional[str]) -> Optional[str]:
    if not email or not isinstance(email, str):
        return None
    e = email.strip().lower()
    return e if "@" in e else None


def email_hash(email: Optional[str]) -> Optional[str]:
    e = normalize_email(email)
    return hashlib.sha256(e.encode("utf-8")).hexdigest() if e else None


def phone_digits(phone: Optional[str]) -> Optional[str]:
    if not phone or not isinstance(phone, str):
        return None
    d = "".join(c for c in phone if c.isdigit())
    return d or None


def phone_hash(phone: Optional[str]) -> Optional[str]:
    d = phone_digits(phone)
    return hashlib.sha256(d.encode("utf-8")).hexdigest() if d else None


def mask_email(email: Optional[str]) -> Optional[str]:
    e = normalize_email(email)
    if not e:
        return None
    local, _, domain = e.partition("@")
    return f"{local[:2]}***@{domain}"


def pii_fields(email: Optional[str], phone: Optional[str]) -> dict:
    """Non-null hash/mask columns for a row (empty dict when nothing to store)."""
    out = {
        "email_hash":   email_hash(email),
        "phone_hash":   phone_hash(phone),
        "email_masked": mask_email(email),
    }
    return {k: v for k, v in out.items() if v}
