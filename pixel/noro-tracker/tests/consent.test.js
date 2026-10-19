import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createConsent } from '../core/consent.js';
import { buildCartAttributes } from '../ecommerce/shopify.js';

// ── Consent module — unit ─────────────────────────────────────────────────────

describe('createConsent — defaults', () => {
  it('grants all three purposes by default', () => {
    const c = createConsent();
    expect(c.isGranted('tracking')).toBe(true);
    expect(c.isGranted('advertising')).toBe(true);
    expect(c.isGranted('analytics')).toBe(true);
  });

  it('returns false for unknown purposes', () => {
    const c = createConsent();
    expect(c.isGranted('unknown')).toBe(false);
    expect(c.isGranted('')).toBe(false);
  });
});

describe('createConsent — grant / revoke', () => {
  it('revoke denies the purpose without touching others', () => {
    const c = createConsent();
    c.revoke('analytics');
    expect(c.isGranted('analytics')).toBe(false);
    expect(c.isGranted('tracking')).toBe(true);
    expect(c.isGranted('advertising')).toBe(true);
  });

  it('grant restores a previously revoked purpose', () => {
    const c = createConsent();
    c.revoke('analytics');
    c.grant('analytics');
    expect(c.isGranted('analytics')).toBe(true);
  });

  it('double-revoke is idempotent', () => {
    const c = createConsent();
    c.revoke('advertising');
    c.revoke('advertising');
    expect(c.isGranted('advertising')).toBe(false);
  });

  it('getState reflects all current grants', () => {
    const c = createConsent();
    c.revoke('advertising');
    expect(c.getState()).toMatchObject({ tracking: true, advertising: false, analytics: true });
  });
});

describe('createConsent — initialGrants override', () => {
  it('analytics: false in initialGrants starts revoked', () => {
    const c = createConsent({ analytics: false });
    expect(c.isGranted('analytics')).toBe(false);
    expect(c.isGranted('tracking')).toBe(true);
  });

  it('all three revoked via initialGrants', () => {
    const c = createConsent({ tracking: false, advertising: false, analytics: false });
    expect(c.isGranted('tracking')).toBe(false);
    expect(c.isGranted('advertising')).toBe(false);
    expect(c.isGranted('analytics')).toBe(false);
  });
});

// ── ga_client_id consent gate — buildCartAttributes _gcid ────────────────────
//
// The tracker sets ctx.ga_client_id = null when analytics consent is not
// granted. buildCartAttributes maps ctx.ga_client_id → _gcid.

describe('buildCartAttributes — _gcid analytics consent gate', () => {
  it('_gcid is empty string when ga_client_id is null (analytics not consented)', () => {
    const ctx = { visitor_id: 'v1', session_id: 's1', touch: {}, ga_client_id: null };
    const attrs = buildCartAttributes(ctx, false);
    expect(attrs['_gcid']).toBe('');
  });

  it('_gcid carries ga_client_id value when analytics is consented', () => {
    const ctx = { visitor_id: 'v1', session_id: 's1', touch: {}, ga_client_id: '1234567890.9876543210' };
    const attrs = buildCartAttributes(ctx, false);
    expect(attrs['_gcid']).toBe('1234567890.9876543210');
  });

  it('_etv and _noro_sid are always present regardless of ga_client_id', () => {
    const ctx = { visitor_id: 'vid-abc', session_id: 'sid-xyz', touch: {}, ga_client_id: null };
    const attrs = buildCartAttributes(ctx, false);
    expect(attrs['_etv']).toBe('vid-abc');
    expect(attrs['_noro_sid']).toBe('sid-xyz');
  });

  it('_gcid absent from attrs when metaEnabled=false (no Meta keys added)', () => {
    const ctx = { visitor_id: 'v1', session_id: 's1', touch: {}, ga_client_id: null, fbp: 'fbp123' };
    const attrs = buildCartAttributes(ctx, false);
    expect(attrs['_fbp']).toBeUndefined();
    expect(attrs['_gcid']).toBe(''); // empty, not undefined
  });
});

// ── ga_client_id consent gate — tracker._buildContext ────────────────────────
//
// We test the gate end-to-end: tracker is created with modules.google=true
// (so googleSignals.ga_client_id would be populated from _ga cookie), then
// we revoke analytics consent and verify the fired event has ga_client_id=null.
//
// Transport (fetch) is stubbed so no real HTTP calls happen.
// The send() body is { events: [...] } — access events[0].

describe('tracker — ga_client_id absent when analytics consent revoked', () => {
  let capturedBody;

  beforeEach(() => {
    capturedBody = null;
    // Stub fetch to capture the JSON body sent to the noro ingest endpoint
    vi.stubGlobal('fetch', vi.fn(async (_url, opts) => {
      capturedBody = JSON.parse(opts?.body || '{"events":[]}');
      return { ok: true, status: 200 };
    }));
    // Plant a _ga cookie so google.captureSignals() can parse a ga_client_id
    document.cookie = '_ga=GA1.2.111111111.1700000000; path=/';
  });

  it('ga_client_id is present in event when analytics consent granted', async () => {
    const { createTracker } = await import('../tracker.js');
    const tracker = createTracker({
      client: 'test',
      ingestToken: 'tok',
      apiBase: 'https://noro-test.example',
      modules: { noro: true, google: true, ga4: false, meta: false, tiktok: false, pinterest: false },
    });
    // analytics is granted by default; _ga cookie is set
    await tracker.fire('page_view', {});
    await vi.waitFor(() => capturedBody !== null);
    const event = capturedBody.events[0];
    expect(event).toBeDefined();
    expect(event.ga_client_id).toBe('111111111.1700000000');
  });

  it('ga_client_id is null in event when analytics consent revoked', async () => {
    const { createTracker } = await import('../tracker.js');
    const tracker = createTracker({
      client: 'test2',
      ingestToken: 'tok2',
      apiBase: 'https://noro-test.example',
      modules: { noro: true, google: true, ga4: false, meta: false, tiktok: false, pinterest: false },
    });
    // Revoke analytics consent before firing
    tracker._consent.revoke('analytics');
    await tracker.fire('page_view', {});
    await vi.waitFor(() => capturedBody !== null);
    const event = capturedBody.events[0];
    expect(event).toBeDefined();
    // ga_client_id MUST be null — even though _ga cookie is planted
    expect(event.ga_client_id).toBeNull();
  });
});
