"""Pure helpers of the Shopify app OAuth install (no DB, no network)."""
import hashlib
import hmac

from app.routers import shopify_app as sa


def _sign(params: dict, secret: str) -> str:
    msg = "&".join(sorted(f"{k}={v}" for k, v in params.items()))
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def test_query_hmac_ok_accepts_valid_and_rejects_tampered():
    secret = "shpss_test"
    params = {"code": "abc", "shop": "e-nutri.myshopify.com", "state": "1.x.y", "timestamp": "1700000000"}
    signed = list(params.items()) + [("hmac", _sign(params, secret))]
    assert sa.query_hmac_ok(signed, secret)
    tampered = [(k, ("evil.myshopify.com" if k == "shop" else v)) for k, v in signed]
    assert not sa.query_hmac_ok(tampered, secret)
    assert not sa.query_hmac_ok(list(params.items()), secret)  # no hmac
    assert not sa.query_hmac_ok(signed, "other")


def test_state_roundtrip_and_expiry():
    st = sa.make_state("enutri-4sph", "s", now=1_000_000)
    assert sa.state_ok(st, "enutri-4sph", "s", now=1_000_000 + 60)
    assert not sa.state_ok(st, "outro-cliente", "s", now=1_000_000 + 60)
    assert not sa.state_ok(st, "enutri-4sph", "x", now=1_000_000 + 60)
    assert not sa.state_ok(st, "enutri-4sph", "s", now=1_000_000 + 16 * 60)
    assert not sa.state_ok("garbage", "enutri-4sph", "s")


def test_shop_validation():
    assert sa.normalize_shop("https://E-Nutri.myshopify.com/admin") == "e-nutri.myshopify.com"
    assert sa.valid_shop("e-nutri.myshopify.com")
    assert not sa.valid_shop("e-nutri.myshopify.com.evil.com")
    assert not sa.valid_shop("enutri.com.br")


def test_scopes_are_read_only():
    assert all(s.startswith("read_") for s in sa.SCOPES.split(","))
