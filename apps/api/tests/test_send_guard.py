"""
Conversion send guard (CONVERSION_SENDS_DISABLED) — kill switch tests.

Covers:
  §1  Env parsing: empty, one client, several, "*"
  §2  Matching by slug, pixel_id or UUID (alias lookup) + cache
  §3  Fail-closed when the client lookup fails
  §4  Coverage: every module that calls a conversion send function is guarded
"""
from __future__ import annotations

import os
import pathlib
import re

from app.core import send_guard as sg

CLIENT_ROW = {"id": "aaaaaaaa-0000-0000-0000-000000000001", "client_id": "enutri", "pixel_id": "enutri-qe5p"}


class _Res:
    def __init__(self, data):
        self.data = data


class _SB:
    """clients lookup by pixel_id / client_id / id."""

    def __init__(self, rows=(CLIENT_ROW,), fail=False):
        self.rows, self.fail, self.lookups = list(rows), fail, 0
        self._col = self._val = None

    def table(self, _):
        return self

    def select(self, *_):
        return self

    def eq(self, col, val):
        self._col, self._val = col, val
        return self

    def limit(self, _):
        return self

    def execute(self):
        self.lookups += 1
        if self.fail:
            raise RuntimeError("db down")
        return _Res([r for r in self.rows if r.get(self._col) == self._val])


def _with_env(value):
    sg.clear_cache()
    if value is None:
        os.environ.pop(sg.ENV_VAR, None)
    else:
        os.environ[sg.ENV_VAR] = value


class TestEnvParsing:
    def test_empty_blocks_nothing(self):
        _with_env("")
        assert sg.sends_blocked("enutri-qe5p", sb=_SB()) is False
        _with_env(None)
        assert sg.sends_blocked("enutri-qe5p", sb=_SB()) is False

    def test_star_blocks_everyone_without_lookup(self):
        _with_env("*")
        sb = _SB()
        assert sg.sends_blocked("any-client", sb=sb) is True and sb.lookups == 0

    def test_list_parsing(self):
        _with_env(" Enutri , dipua-qe5p ,, ")
        assert sg.blocked_identifiers() == frozenset({"enutri", "dipua-qe5p"})


class TestMatching:
    def test_slug_in_env_matches_pixel_id_send(self):
        _with_env("enutri")
        assert sg.sends_blocked("enutri-qe5p", sb=_SB()) is True

    def test_pixel_in_env_matches_uuid_send(self):
        _with_env("enutri-qe5p")
        assert sg.sends_blocked(CLIENT_ROW["id"], sb=_SB()) is True

    def test_other_clients_not_blocked(self):
        _with_env("enutri")
        other = {"id": "bbbbbbbb-0000-0000-0000-000000000002", "client_id": "dipua", "pixel_id": "dipua-x1"}
        assert sg.sends_blocked("dipua-x1", sb=_SB(rows=(CLIENT_ROW, other))) is False

    def test_direct_hit_needs_no_lookup(self):
        _with_env("enutri-qe5p")
        sb = _SB()
        assert sg.sends_blocked("ENUTRI-QE5P", sb=sb) is True and sb.lookups == 0

    def test_alias_cache(self):
        _with_env("enutri")
        sb = _SB()
        sg.sends_blocked("enutri-qe5p", sb=sb)
        first = sb.lookups
        sg.sends_blocked("enutri-qe5p", sb=sb)
        assert sb.lookups == first


class TestFailClosed:
    def test_lookup_error_blocks(self):
        _with_env("enutri")
        assert sg.sends_blocked("unknown-pixel", sb=_SB(fail=True)) is True

    def test_lookup_error_irrelevant_when_list_empty(self):
        _with_env("")
        assert sg.sends_blocked("unknown-pixel", sb=_SB(fail=True)) is False


class TestCoverage:
    SEND_CALL = re.compile(
        r"\b(meta_capi|ga4|ga4_svc|google_ads|tiktok_capi|pinterest_capi)\.(send_\w+)\("
    )
    PLATFORM_MODULES = {"meta_capi.py", "ga4.py", "google_ads.py", "tiktok_capi.py", "pinterest_capi.py"}

    def test_every_send_site_is_guarded(self):
        root = pathlib.Path(__file__).resolve().parents[1] / "app"
        unguarded = []
        for path in root.rglob("*.py"):
            if path.name in self.PLATFORM_MODULES or path.name == "send_guard.py":
                continue
            src = path.read_text(encoding="utf-8")
            if self.SEND_CALL.search(src) and "send_guard.sends_blocked(" not in src:
                unguarded.append(str(path.relative_to(root)))
        assert not unguarded, f"conversion sends without CONVERSION_SENDS_DISABLED guard: {unguarded}"

    def teardown_method(self, _):
        _with_env(None)
