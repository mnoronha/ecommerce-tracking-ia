/**
 * Noro Tracker v4 — Main orchestrator
 *
 * createTracker(config) wires all modules together and returns a
 * { fire, track, pageView } public API.
 *
 * No module code runs unless its feature flag is enabled in config.modules.
 */

import { createVisitor, generateUUID } from './core/visitor.js';
import { createSession }               from './core/session.js';
import { createAttribution }           from './core/attribution.js';
import { createDedup, purchaseEventId }from './core/dedup.js';
import { createConsent }               from './core/consent.js';
import { createQueue }                 from './core/queue.js';
import { send }                        from './core/transport.js';
import { buildEvent, VALID_EVENTS }    from './core/events.js';

import { createNoroDestination }       from './destinations/noro.js';
import { createGA4Destination }        from './destinations/ga4.js';
import { createGoogleModule }          from './destinations/google.js';
import { createMetaDestination }       from './destinations/meta.js';
import { createTikTokDestination }     from './destinations/tiktok.js';
import { createPinterestDestination }  from './destinations/pinterest.js';

import {
  detectPage,
  extractOrderDetails,
  syncCartAttributes,
  buildCartAttributes,
  watchAddToCart,
  watchBeginCheckout,
} from './ecommerce/shopify.js';

export function createTracker(config) {
  const modules = config.modules || {};

  // ── Core ─────────────────────────────────────────────────────────────────────
  const visitor     = createVisitor();
  const session     = createSession();
  const attribution = createAttribution();
  const dedup       = createDedup();
  const consent     = createConsent();

  // ── Destinations ─────────────────────────────────────────────────────────────
  const noro      = modules.noro      ? createNoroDestination(config)      : null;
  const ga4       = modules.ga4       ? createGA4Destination(config)       : null;
  const google    = modules.google    ? createGoogleModule()               : null;
  const meta      = createMetaDestination(config); // handles disabled case internally
  const tiktok    = createTikTokDestination(config);
  const pinterest = createPinterestDestination(config);

  if (modules.meta)      meta.init();
  if (modules.tiktok)    tiktok.init();
  if (modules.pinterest) pinterest.init();

  // ── Queue (for Noro ingest retries) ─────────────────────────────────────────
  const _noroQueue = noro
    ? createQueue(async (event) => noro.sendEvents([event]))
    : null;

  // ── Attribution + Google signals ─────────────────────────────────────────────
  attribution.update();
  const googleSignals = google ? google.captureSignals() : {};

  function _buildContext() {
    const touch = attribution.getCurrentTouch() || {};
    return {
      visitor_id:   visitor.getVisitorId(),
      session_id:   session.touch(),
      touch,
      url:          typeof location !== 'undefined' ? location.href : null,
      referrer:     typeof document !== 'undefined' ? document.referrer : null,
      gclid:        googleSignals.gclid   || null,
      gbraid:       googleSignals.gbraid  || null,
      wbraid:       googleSignals.wbraid  || null,
      // ga_client_id requires analytics consent (LGPD)
      ga_client_id: (google && consent.isGranted('analytics')) ? (googleSignals.ga_client_id || null) : null,
      fbp:          modules.meta ? meta.getFbp() : null,
      fbc:          modules.meta ? meta.getFbc() : null,
    };
  }

  // ── Fire event to all enabled destinations ───────────────────────────────────
  async function fire(canonicalName, data = {}, overrideEventId = null) {
    if (!VALID_EVENTS.includes(canonicalName)) return;
    if (!consent.isGranted('tracking'))        return;

    const ctx     = _buildContext();
    const eventId = overrideEventId || generateUUID();
    const event   = buildEvent(canonicalName, eventId, data, ctx);

    // Noro ingest
    if (noro && _noroQueue) {
      const result = await noro.sendEvents([event]);
      if (!result.ok) {
        _noroQueue.enqueue(event);
      }
    }

    // GA4 dataLayer bridge
    if (ga4 && consent.isGranted('analytics')) {
      ga4.sendEvent(canonicalName, { ...data, url: ctx.url, referrer: ctx.referrer });
    }

    // Meta Pixel
    if (modules.meta && consent.isGranted('advertising')) {
      meta.sendEvent(canonicalName, data, eventId);
    }

    // TikTok / Pinterest
    if (modules.tiktok)    tiktok.sendEvent(canonicalName, data, eventId);
    if (modules.pinterest) pinterest.sendEvent(canonicalName, data, eventId);

    return eventId;
  }

  // ── Shopify-specific integration ─────────────────────────────────────────────

  function _syncBridge() {
    if (!config.shopify || !config.shopify.bridge) return;
    const ctx   = _buildContext();
    const attrs = buildCartAttributes(ctx, !!modules.meta);
    syncCartAttributes(attrs);
  }

  function _setupShopifyEvents() {
    const page = detectPage();

    // page_view — always
    fire('page_view', {});
    _syncBridge();

    // view_item — product page only (data injected via Liquid into __noroProductCtx)
    if (page.type === 'product') {
      const pd = (typeof window !== 'undefined' && window.__noroProductCtx) || {};
      fire('view_item', {
        product_id:       pd.product_id       || null,
        product_name:     pd.product_name     || null,
        product_price:    pd.product_price    || null,
        product_category: pd.product_category || null,
        product_quantity: 1,
      });
    }

    // add_to_cart — confirmed via fetch intercept
    watchAddToCart((cartData) => {
      fire('add_to_cart', cartData);
      _syncBridge();
    });

    // begin_checkout — intent level
    watchBeginCheckout((meta) => {
      fire('begin_checkout', {
        properties: { intent: meta.intent, trigger: meta.trigger },
      });
    });

    // purchase — confirmed order only
    if (page.type === 'order_status') {
      _maybeTrackPurchase();
    }
  }

  async function _maybeTrackPurchase() {
    const details = extractOrderDetails();
    if (!details.order_id) return;
    if (dedup.isDuplicate('purchase_' + details.order_id)) return;

    const eventId = await purchaseEventId(details.order_id);
    await fire('purchase', details, eventId);
    dedup.mark('purchase_' + details.order_id);
  }

  // ── Flush retry queue periodically ───────────────────────────────────────────
  if (_noroQueue && typeof window !== 'undefined') {
    window.addEventListener('focus', () => _noroQueue.flush());
    // Flush on visibility change (tab comes back into view)
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') _noroQueue.flush();
      });
    }
  }

  // ── Public API ────────────────────────────────────────────────────────────────
  return {
    fire,
    // Manual track for custom events from theme code
    track: (name, data) => fire(name, data),
    // Setup all Shopify auto-events (call once on DOMContentLoaded)
    setup: _setupShopifyEvents,
    // Expose sub-modules for testing / advanced usage
    _visitor:     visitor,
    _session:     session,
    _attribution: attribution,
    _dedup:       dedup,
    _consent:     consent,
    _queue:       _noroQueue,
    _google:      google,
    _meta:        meta,
  };
}

// ── Auto-init from window.__NORO_CONFIG__ ─────────────────────────────────────
// The Shopify Liquid loader sets window.__NORO_CONFIG__ before this script loads.

if (typeof window !== 'undefined' && window.__NORO_CONFIG__) {
  const tracker = createTracker(window.__NORO_CONFIG__);
  window.NoroTracker = tracker;

  const _setup = () => tracker.setup();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _setup);
  } else {
    _setup();
  }
}
