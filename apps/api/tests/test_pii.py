"""LGPD — customer identifiers by hash (core/pii.py)."""
from __future__ import annotations

import hashlib
import pathlib

from app.core import pii


class TestPii:
    def test_email_hash_matches_meta_spec(self):
        assert pii.email_hash("  Ana@Ex.COM ") == hashlib.sha256(b"ana@ex.com").hexdigest()
        assert pii.email_hash("not-an-email") is None and pii.email_hash(None) is None

    def test_phone_hash_digits_only(self):
        assert pii.phone_hash("+55 (17) 99152-7793") == hashlib.sha256(b"5517991527793").hexdigest()
        assert pii.phone_hash("abc") is None

    def test_mask(self):
        assert pii.mask_email("Ana.Silva@Gmail.com") == "an***@gmail.com"

    def test_pii_fields_never_contains_raw(self):
        f = pii.pii_fields("ana@ex.com", "11999998888")
        assert set(f) == {"email_hash", "phone_hash", "email_masked"}
        assert "ana@ex.com" not in f.values() and "11999998888" not in f.values()
        assert pii.pii_fields(None, None) == {}


class TestNoRawEmailLookups:
    """Lookups that used raw email must use email_hash now."""

    FILES = [
        "app/services/writer.py",
        "app/routers/ecommerce_webhooks.py",
        "app/routers/klaviyo_webhook.py",
        "app/routers/lgpd.py",
        "app/services/attribution_engine.py",
        "app/services/ai_analyst.py",
    ]

    def test_no_eq_or_ilike_on_raw_email(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        bad = []
        for rel in self.FILES:
            src = (root / rel).read_text(encoding="utf-8")
            for needle in ('.eq("email",', ".eq('email',", '.ilike("email",', '.in_(\n                    "email",'):
                if needle in src:
                    bad.append(f"{rel}: {needle}")
        assert not bad, bad
