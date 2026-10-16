/* Noro Tracker v4.3.1 — Shopify storefront collector
 * 2026-10-16
 *
 * Scope:
 * - Storefront only: page_view, view_item, confirmed add_to_cart, checkout intent.
 * - Checkout/thank-you: companion Shopify Custom Web Pixel (noro-shopify-web-pixel-v2.js).
 * - Purchase/order truth: server reconciliation (orders/paid webhook + Admin API backfill).
 *
 * Privacy:
 * - No raw email, phone, DOB, or Facebook Login ID.
 * - Ad click IDs (gclid/gbraid/wbraid/fbclid/fbc/fbp) are only persisted and sent
 *   when advertising consent is granted. Without consent they are held in
 *   sessionStorage only (never sent) and promoted if consent arrives later.
 * - Sent URLs are sanitized: path + UTM params only.
 *
 * v4.3.1 changes vs v4.3:
 * - GA4 session id captured from the _ga_<MEASUREMENT_ID> cookie (GS1 and GS2
 *   formats), sent as ga_session_id and bridged to the cart as _ga_sid so the
 *   server-side purchase lands in the visitor's original GA4 session.
 *
 * v4.3 changes vs v4.2:
 * - click IDs removed from touch/landing_page/url (consent leak fixed)
 * - buildEvent uses consent-gated ctx click IDs only
 * - no double add_to_cart on AJAX themes (pending form marker cleared)
 * - Shopify Customer Privacy API loaded explicitly; consent changes re-sync
 * - consent revocation clears stored click ID cookies + cart attributes
 * - cart attribute cache keyed by Shopify cart token; forced on add_to_cart
 * - currency defaults from config
 * - GA4 adapter restored to GA4 ecommerce schema
 * - fbc built from fbclid when Meta module is enabled (fb.1.<ts>.<fbclid>)
 * - consent state + tracker version recorded on every event
 * - configurable ingestUrl (first-party subdomain ready)
 * - dead code removed
 */
(function () {
  "use strict";

  if (typeof window === "undefined") return;
  if (window.__noroLoaded) return;
  window.__noroLoaded = true;

  const VERSION = "4.3.1";

  const VALID_EVENTS = Object.freeze([
    "page_view",
    "view_item",
    "add_to_cart",
    "begin_checkout",
    "purchase"
  ]);

  const CLICK_PARAMS = Object.freeze(["gclid", "gbraid", "wbraid", "fbclid"]);
  const UTM_PARAMS = Object.freeze(["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"]);

  const CK = {
    visitor: "_noro_v",
    first: "_noro_ft",
    last: "_noro_lt",
    gclid: "_noro_gclid",
    gbraid: "_noro_gbraid",
    wbraid: "_noro_wbraid",
    fbclid: "_noro_fbclid",
    fbcBuilt: "_noro_fbc",
    metaFbp: "_fbp",
    metaFbc: "_fbc",
    shopifyCart: "cart"
  };

  const SK = {
    session: "_noro_s",
    sessionExp: "_noro_se",
    sessionTouch: "_noro_st",
    queue: "_noro_q",
    cartAttrs: "_noro_ca",
    pendingFormATC: "_noro_pending_atc",
    heldClickIds: "_noro_held_ck"
  };

  const SESSION_TTL_MS = 30 * 60 * 1000;
  const FIRST_TOUCH_DAYS = 365;
  const LAST_TOUCH_DAYS = 30;
  const CLICK_ID_DAYS = 90;
  const QUEUE_MAX = 50;
  const QUEUE_MAX_AGE_MS = 24 * 60 * 60 * 1000;
  const QUEUE_MAX_RETRIES = 4;
  const PENDING_ATC_MAX_AGE_MS = 30000;
  const BEGIN_CHECKOUT_DEDUP_MS = 3000;

  /* ------------------------------------------------------------------ */
  /* Utilities                                                           */
  /* ------------------------------------------------------------------ */

  function safeJSONParse(value, fallback) {
    try { return JSON.parse(value); } catch (_) { return fallback; }
  }

  function uuid() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      const r = Math.random() * 16 | 0;
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  function cookieDomain(hostname) {
    if (!hostname || hostname.indexOf(".") === -1 || /^\d+(\.\d+)+$/.test(hostname)) return "";
    if (/(^|\.)myshopify\.com$/i.test(hostname)) return "";
    const parts = hostname.split(".");
    const multi = new Set([
      "com.br", "co.uk", "co.jp", "co.kr", "co.in", "com.au", "com.ar", "com.mx",
      "com.co", "com.pe", "com.sg", "com.hk", "com.tw", "com.tr", "com.ua"
    ]);
    const last2 = parts.slice(-2).join(".");
    return multi.has(last2) && parts.length >= 3
      ? "." + parts.slice(-3).join(".")
      : "." + last2;
  }

  function setCookie(name, value, days) {
    const expires = new Date(Date.now() + days * 86400000).toUTCString();
    let domain = "";
    try { domain = cookieDomain(location.hostname); } catch (_) {}
    const valueText = encodeURIComponent(String(value));
    let cookie = `${name}=${valueText}; expires=${expires}; path=/; SameSite=Lax; Secure`;
    if (domain) cookie += `; domain=${domain}`;
    document.cookie = cookie;

    if (domain && getCookie(name) !== String(value)) {
      document.cookie = `${name}=${valueText}; expires=${expires}; path=/; SameSite=Lax; Secure`;
    }
  }

  function deleteCookie(name) {
    const past = "Thu, 01 Jan 1970 00:00:00 GMT";
    let domain = "";
    try { domain = cookieDomain(location.hostname); } catch (_) {}
    document.cookie = `${name}=; expires=${past}; path=/`;
    if (domain) document.cookie = `${name}=; expires=${past}; path=/; domain=${domain}`;
  }

  function getCookie(name) {
    const match = document.cookie.match(new RegExp("(?:^|;\\s*)" + name + "=([^;]*)"));
    if (!match) return null;
    try { return decodeURIComponent(match[1]); } catch (_) { return match[1]; }
  }

  function storageGet(storage, key) {
    try { return storage.getItem(key); } catch (_) { return null; }
  }

  function storageSet(storage, key, value) {
    try { storage.setItem(key, value); return true; } catch (_) { return false; }
  }

  function storageRemove(storage, key) {
    try { storage.removeItem(key); } catch (_) {}
  }

  function readCookieJSON(name) {
    const raw = getCookie(name);
    return raw ? safeJSONParse(raw, null) : null;
  }

  function writeCookieJSON(name, value, days) {
    setCookie(name, JSON.stringify(value), days);
  }

  function getParam(name) {
    try { return new URLSearchParams(location.search).get(name); } catch (_) { return null; }
  }

  function normalizeClickId(value) {
    if (!value || typeof value !== "string") return null;
    const v = value.trim();
    if (v.length < 6 || v.length > 512) return null;
    if (!/^[A-Za-z0-9._~:%+\-]+$/.test(v)) return null;
    return v;
  }

  /** Path + UTM params only. Click IDs and any other query param are dropped. */
  function sanitizeUrl(urlString) {
    try {
      const u = new URL(urlString || location.href);
      const out = new URL(u.origin + u.pathname);
      UTM_PARAMS.forEach((key) => {
        const value = u.searchParams.get(key);
        if (value) out.searchParams.set(key, value.slice(0, 200));
      });
      return out.pathname + out.search;
    } catch (_) {
      return "/";
    }
  }

  function sanitizeReferrer(ref) {
    if (!ref) return null;
    try {
      const u = new URL(ref);
      return u.origin + u.pathname;
    } catch (_) {
      return null;
    }
  }

  /* ------------------------------------------------------------------ */
  /* Identity                                                            */
  /* ------------------------------------------------------------------ */

  function createVisitor() {
    let memory = null;
    return {
      getVisitorId() {
        if (memory) return memory;
        let id = getCookie(CK.visitor);
        if (!id) {
          id = uuid();
          setCookie(CK.visitor, id, 365);
        }
        memory = id;
        return id;
      }
    };
  }

  function createSession() {
    let memoryId = null;
    let memoryExp = 0;

    function read() {
      const id = storageGet(localStorage, SK.session);
      const exp = parseInt(storageGet(localStorage, SK.sessionExp) || "0", 10);
      if (id && exp > Date.now()) return { id, exp };
      if (memoryId && memoryExp > Date.now()) return { id: memoryId, exp: memoryExp };
      return null;
    }

    function write(id, exp) {
      const a = storageSet(localStorage, SK.session, id);
      const b = storageSet(localStorage, SK.sessionExp, String(exp));
      if (!a || !b) {
        memoryId = id;
        memoryExp = exp;
      }
    }

    return {
      touch() {
        const current = read();
        const id = current ? current.id : uuid();
        write(id, Date.now() + SESSION_TTL_MS);
        return id;
      }
    };
  }

  /* ------------------------------------------------------------------ */
  /* Consent                                                             */
  /* ------------------------------------------------------------------ */

  function createConsent(config) {
    const defaults = Object.assign(
      { tracking: true, analytics: false, advertising: false },
      (config.consent && config.consent.defaults) || {}
    );

    const state = {
      tracking: defaults.tracking === true,
      analytics: defaults.analytics === true,
      advertising: defaults.advertising === true,
      source: "defaults"
    };

    const listeners = [];

    function sync() {
      const before = state.analytics + "|" + state.advertising;
      try {
        const cp = window.Shopify && window.Shopify.customerPrivacy;
        if (cp) {
          if (typeof cp.analyticsProcessingAllowed === "function") {
            state.analytics = cp.analyticsProcessingAllowed() === true;
          }
          if (typeof cp.marketingAllowed === "function") {
            state.advertising = cp.marketingAllowed() === true;
          }
          state.source = "shopify";
        }
      } catch (_) {}
      const after = state.analytics + "|" + state.advertising;
      if (before !== after) {
        listeners.forEach((fn) => { try { fn(getState()); } catch (_) {} });
      }
    }

    function loadShopifyApi() {
      try {
        if (window.Shopify && window.Shopify.customerPrivacy) {
          sync();
          return;
        }
        if (window.Shopify && typeof window.Shopify.loadFeatures === "function") {
          window.Shopify.loadFeatures(
            [{ name: "consent-tracking-api", version: "0.1" }],
            function (error) { if (!error) sync(); }
          );
        }
      } catch (_) {}
    }

    function getState() {
      return {
        tracking: state.tracking,
        analytics: state.analytics,
        advertising: state.advertising,
        source: state.source
      };
    }

    loadShopifyApi();
    document.addEventListener("visitorConsentCollected", sync);

    return {
      sync,
      isGranted(type) {
        return (type === "tracking" || type === "analytics" || type === "advertising") && state[type] === true;
      },
      onChange(fn) { listeners.push(fn); },
      getState
    };
  }

  /* ------------------------------------------------------------------ */
  /* Ad click IDs (consent-gated)                                        */
  /* ------------------------------------------------------------------ */

  function createClickIds(consent, config) {
    const metaEnabled = !!(config.modules && config.modules.meta);

    function readFromUrl() {
      const out = {};
      CLICK_PARAMS.forEach((p) => {
        const v = normalizeClickId(getParam(p));
        if (v) out[p] = v;
      });
      return out;
    }

    function persist(ids, capturedAt) {
      CLICK_PARAMS.forEach((p) => {
        if (ids[p]) setCookie(CK[p], ids[p], CLICK_ID_DAYS);
      });
      if (ids.fbclid) {
        setCookie(CK.fbcBuilt, "fb.1." + (capturedAt || Date.now()) + "." + ids.fbclid, CLICK_ID_DAYS);
      }
    }

    function hold(ids) {
      const held = safeJSONParse(storageGet(sessionStorage, SK.heldClickIds) || "{}", {}) || {};
      storageSet(sessionStorage, SK.heldClickIds, JSON.stringify(Object.assign(held, ids, { ts: Date.now() })));
    }

    function capture() {
      const fromUrl = readFromUrl();
      if (!Object.keys(fromUrl).length) return;
      if (consent.isGranted("advertising")) persist(fromUrl, Date.now());
      else hold(fromUrl);
    }

    function promoteHeld() {
      if (!consent.isGranted("advertising")) return;
      const held = safeJSONParse(storageGet(sessionStorage, SK.heldClickIds) || "null", null);
      if (!held) return;
      persist(held, held.ts);
      storageRemove(sessionStorage, SK.heldClickIds);
    }

    function purge() {
      CLICK_PARAMS.forEach((p) => deleteCookie(CK[p]));
      deleteCookie(CK.fbcBuilt);
    }

    function get() {
      if (!consent.isGranted("advertising")) return {};
      const out = {};
      CLICK_PARAMS.forEach((p) => {
        const v = normalizeClickId(getCookie(CK[p]));
        if (v) out[p] = v;
      });
      if (metaEnabled) {
        out.fbp = getCookie(CK.metaFbp) || null;
        out.fbc = getCookie(CK.metaFbc) || getCookie(CK.fbcBuilt) || null;
      }
      return out;
    }

    function urlClickSource() {
      const ids = readFromUrl();
      return CLICK_PARAMS.find((p) => ids[p]) || null;
    }

    return { capture, promoteHeld, purge, get, urlClickSource };
  }

  /* ------------------------------------------------------------------ */
  /* Attribution (UTMs only — no click IDs stored here)                  */
  /* ------------------------------------------------------------------ */

  function createAttribution(clickIds) {
    function readTouch() {
      const touch = {};
      UTM_PARAMS.forEach((p) => {
        const v = getParam(p);
        if (v) touch[p] = v.slice(0, 200);
      });

      // Only the *type* of click (e.g. "gclid"), never the identifier itself.
      const clickSource = clickIds.urlClickSource();
      if (clickSource) touch.click_source = clickSource;

      touch.landing_page = sanitizeUrl(location.href);
      const ref = sanitizeReferrer(document.referrer);
      if (ref) touch.referrer = ref;
      touch.ts = Date.now();

      const hasMarketingSignal = Boolean(touch.utm_source || touch.click_source);
      return { touch, hasMarketingSignal };
    }

    function update() {
      const { touch, hasMarketingSignal } = readTouch();
      if (!hasMarketingSignal) return touch;

      if (!storageGet(sessionStorage, SK.sessionTouch)) {
        storageSet(sessionStorage, SK.sessionTouch, JSON.stringify(touch));
      }
      if (!readCookieJSON(CK.first)) {
        writeCookieJSON(CK.first, touch, FIRST_TOUCH_DAYS);
      }
      writeCookieJSON(CK.last, touch, LAST_TOUCH_DAYS);
      return touch;
    }

    function getSessionTouch() {
      const raw = storageGet(sessionStorage, SK.sessionTouch);
      return raw ? safeJSONParse(raw, null) : null;
    }

    return {
      update,
      getFirstTouch: () => readCookieJSON(CK.first),
      getSessionTouch,
      getLastTouch: () => readCookieJSON(CK.last),
      getCurrentTouch() {
        return readCookieJSON(CK.last) || getSessionTouch() || readCookieJSON(CK.first);
      }
    };
  }

  /* ------------------------------------------------------------------ */
  /* Transport + retry queue                                             */
  /* ------------------------------------------------------------------ */

  function ingestUrl(config) {
    if (config.ingestUrl) return config.ingestUrl;
    return `${config.apiBase}/agency/v1/clients/${config.client}/tracking/events`;
  }

  async function postEvents(config, events) {
    try {
      const response = await fetch(ingestUrl(config), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": "Bearer " + config.ingestToken
        },
        body: JSON.stringify({ events }),
        keepalive: true
      });
      return { ok: response.ok, status: response.status };
    } catch (err) {
      return { ok: false, error: err && err.message ? err.message : "network_error" };
    }
  }

  function isRetryable(result) {
    if (!result || result.ok) return false;
    if (result.error) return true;
    const status = Number(result.status || 0);
    return status === 408 || status === 425 || status === 429 || status >= 500;
  }

  function retryDelay(retries) {
    return Math.min(60000, Math.pow(2, retries) * 1000);
  }

  function createQueue(sendOne) {
    let flushing = false;

    function read() {
      return safeJSONParse(storageGet(localStorage, SK.queue) || "[]", []) || [];
    }

    function prune(items) {
      const cutoff = Date.now() - QUEUE_MAX_AGE_MS;
      return items.filter((x) => x && x.event && x.enqueueTime > cutoff && x.retries < QUEUE_MAX_RETRIES);
    }

    function write(items) {
      storageSet(localStorage, SK.queue, JSON.stringify(items));
    }

    function enqueue(event) {
      let items = prune(read());
      if (items.some((x) => x.event.event_id === event.event_id)) return;
      if (items.length >= QUEUE_MAX) items.shift();
      items.push({ event, retries: 0, enqueueTime: Date.now(), nextRetry: Date.now() + 1000 });
      write(items);
    }

    async function flush() {
      if (flushing) return;
      flushing = true;

      try {
        const snapshot = prune(read());
        if (!snapshot.length) return;

        const now = Date.now();
        const due = snapshot.filter((x) => x.nextRetry <= now);
        const keep = snapshot.filter((x) => x.nextRetry > now);
        const processedIds = new Set();

        for (const item of due) {
          processedIds.add(item.event.event_id);
          const result = await sendOne(item.event);
          if (!result.ok && isRetryable(result) && item.retries + 1 < QUEUE_MAX_RETRIES) {
            keep.push({
              event: item.event,
              retries: item.retries + 1,
              enqueueTime: item.enqueueTime,
              nextRetry: Date.now() + retryDelay(item.retries + 1)
            });
          }
        }

        // Merge anything enqueued while this flush was running.
        const latest = prune(read());
        for (const item of latest) {
          const id = item.event.event_id;
          if (processedIds.has(id)) continue;
          if (!keep.some((x) => x.event.event_id === id)) keep.push(item);
        }

        write(prune(keep).slice(-QUEUE_MAX));
      } finally {
        flushing = false;
      }
    }

    return { enqueue, flush };
  }

  /* ------------------------------------------------------------------ */
  /* Event schema                                                        */
  /* ------------------------------------------------------------------ */

  function buildEvent(eventName, eventId, payload, ctx, config) {
    if (!VALID_EVENTS.includes(eventName)) throw new Error("Invalid event " + eventName);
    payload = payload || {};
    ctx = ctx || {};
    const touch = ctx.touch || {};
    const ads = ctx.ads || {};

    return {
      event_name: eventName,
      event_id: eventId,
      occurred_at: new Date().toISOString(),
      session_id: ctx.session_id || null,
      visitor_cookie_id: ctx.visitor_id || null,
      url: sanitizeUrl(location.href),
      referrer: sanitizeReferrer(document.referrer),

      utm_source: touch.utm_source || null,
      utm_medium: touch.utm_medium || null,
      utm_campaign: touch.utm_campaign || null,
      utm_content: touch.utm_content || null,
      utm_term: touch.utm_term || null,

      // Consent-gated: only ctx.ads, never touch.
      gclid: ads.gclid || null,
      gbraid: ads.gbraid || null,
      wbraid: ads.wbraid || null,
      fbclid: ads.fbclid || null,
      fbp: ads.fbp || null,
      fbc: ads.fbc || null,
      ga_client_id: ctx.ga_client_id || null,
      ga_session_id: ctx.ga_session_id || null,

      order_id: payload.order_id || null,
      value: payload.value != null ? Number(payload.value) : null,
      currency: payload.currency || config.currency || null,

      product_id: payload.product_id != null ? String(payload.product_id) : null,
      variant_id: payload.variant_id != null ? String(payload.variant_id) : null,
      sku: payload.sku != null ? String(payload.sku) : null,
      product_name: payload.product_name || null,
      product_price: payload.product_price != null ? Number(payload.product_price) : null,
      product_quantity: payload.product_quantity != null ? Number(payload.product_quantity) : null,
      product_category: payload.product_category || null,

      properties: Object.assign({}, payload.properties || {}, {
        tracker_version: VERSION,
        click_source: touch.click_source || null,
        consent: {
          analytics: !!(ctx.consent && ctx.consent.analytics),
          advertising: !!(ctx.consent && ctx.consent.advertising),
          source: (ctx.consent && ctx.consent.source) || "defaults"
        }
      })
    };
  }

  /**
   * GA4 session id from the _ga_<ID> cookie.
   *   GS1: "GS1.1.1712345678.3.1.1712345999.0.0.0"   -> "1712345678"
   *   GS2: "GS2.1.s1712345678$o3$g1$t1712345999$j0"  -> "1712345678"
   * measurementId "G-DXEZVE9CLN" -> cookie "_ga_DXEZVE9CLN". Without a
   * configured id, uses the only _ga_* cookie present (ambiguous -> null).
   */
  function getGoogleSessionId(measurementId) {
    let raw = null;
    if (measurementId) {
      raw = getCookie("_ga_" + String(measurementId).replace(/^G-/i, ""));
    } else {
      const names = (document.cookie.match(/(?:^|;\s*)(_ga_[A-Z0-9]+)=/g) || [])
        .map((m) => m.replace(/^[;\s]*/, "").replace(/=$/, ""));
      if (names.length === 1) raw = getCookie(names[0]);
    }
    if (!raw) return null;
    let m = raw.match(/^GS1\.\d+\.(\d+)\./);
    if (m) return m[1];
    m = raw.match(/^GS2\.\d+\.s(\d+)/);
    return m ? m[1] : null;
  }

  function getGoogleClientId() {
    const raw = getCookie("_ga");
    if (!raw) return null;
    const parts = raw.split(".");
    return parts.length >= 4 ? parts[2] + "." + parts[3] : null;
  }

  /* ------------------------------------------------------------------ */
  /* GA4 adapter (GA4 ecommerce schema via dataLayer)                    */
  /* ------------------------------------------------------------------ */

  function toGA4Items(p) {
    if (Array.isArray(p.items)) {
      return p.items.map((i) => ({
        item_id: String(i.variant_id || i.product_id || ""),
        item_name: i.product_name || i.name || "",
        item_category: i.product_category || "",
        item_variant: i.sku || String(i.variant_id || ""),
        price: Number(i.price || i.product_price || 0),
        quantity: Number(i.quantity || i.product_quantity || 1)
      }));
    }
    if (!p.product_id && !p.variant_id) return [];
    return [{
      item_id: String(p.variant_id || p.product_id || ""),
      item_name: p.product_name || "",
      item_category: p.product_category || "",
      item_variant: p.sku || String(p.variant_id || ""),
      price: Number(p.product_price || 0),
      quantity: Number(p.product_quantity || 1)
    }];
  }

  function createGA4Adapter(config) {
    const suppressed = new Set((config.ga4 && config.ga4.suppressEvents) || ["page_view"]);
    const currency = config.currency || "BRL";

    function push(eventName, data) {
      window.dataLayer = window.dataLayer || [];
      window.dataLayer.push({ ecommerce: null });
      window.dataLayer.push(Object.assign({ event: eventName }, data));
    }

    return {
      sendEvent(eventName, p) {
        if (suppressed.has(eventName)) return;
        p = p || {};
        if (eventName === "page_view") {
          window.dataLayer = window.dataLayer || [];
          window.dataLayer.push({ event: "page_view", page_location: sanitizeUrl(location.href) });
        } else if (eventName === "view_item") {
          push(eventName, { ecommerce: { currency, value: Number(p.product_price || 0), items: toGA4Items(p) } });
        } else if (eventName === "add_to_cart") {
          push(eventName, {
            ecommerce: {
              currency,
              value: Number(p.product_price || 0) * Number(p.product_quantity || 1),
              items: toGA4Items(p)
            }
          });
        } else if (eventName === "begin_checkout") {
          push(eventName, { ecommerce: { currency, value: Number(p.value || 0), items: toGA4Items(p) } });
        }
        // purchase is never sent from the storefront.
      }
    };
  }

  /* ------------------------------------------------------------------ */
  /* Shopify cart attribution bridge                                     */
  /* ------------------------------------------------------------------ */

  function buildCartAttributes(ctx) {
    const touch = ctx.touch || {};
    const ads = ctx.ads || {};
    const advertising = !!(ctx.consent && ctx.consent.advertising);

    const attrs = {
      _etv: ctx.visitor_id || "",
      _noro_sid: ctx.session_id || "",
      _gcid: ctx.ga_client_id || "",
      _ga_sid: ctx.ga_session_id || "",
      _utm_source: touch.utm_source || "",
      _utm_medium: touch.utm_medium || "",
      _utm_campaign: touch.utm_campaign || "",
      _utm_content: touch.utm_content || "",
      _utm_term: touch.utm_term || "",
      _noro_click_src: touch.click_source || "",
      _noro_consent: "a" + (ctx.consent && ctx.consent.analytics ? 1 : 0) + "m" + (advertising ? 1 : 0),
      _noro_ver: VERSION
    };

    // Always present: empty strings clear values if consent was revoked.
    attrs._gclid = advertising ? (ads.gclid || "") : "";
    attrs._gbraid = advertising ? (ads.gbraid || "") : "";
    attrs._wbraid = advertising ? (ads.wbraid || "") : "";
    attrs._fbclid = advertising ? (ads.fbclid || "") : "";
    attrs._fbp = advertising ? (ads.fbp || "") : "";
    attrs._fbc = advertising ? (ads.fbc || "") : "";

    return attrs;
  }

  function updateCartAttributes(attrs, force) {
    const serialized = JSON.stringify(attrs);
    const cacheKey = () => serialized + "|" + (getCookie(CK.shopifyCart) || "none");
    if (!force && storageGet(sessionStorage, SK.cartAttrs) === cacheKey()) return;

    fetch("/cart/update.js", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ attributes: attrs }),
      credentials: "same-origin",
      keepalive: true
    }).then((res) => {
      // Cart token is read after the response, so a newly created cart is cached correctly.
      if (res && res.ok) storageSet(sessionStorage, SK.cartAttrs, cacheKey());
    }).catch(() => {});
  }

  /* ------------------------------------------------------------------ */
  /* add_to_cart detection                                               */
  /* ------------------------------------------------------------------ */

  function normalizeAddPayload(item) {
    if (!item) return null;
    const variantId = item.variant_id != null ? item.variant_id : item.id;
    if (variantId == null && item.product_id == null) return null;
    return {
      product_id: item.product_id != null ? String(item.product_id) : null,
      variant_id: variantId != null ? String(variantId) : null,
      sku: item.sku || null,
      product_name: item.product_title || item.title || item.product_name || null,
      product_price: item.final_price != null ? Number(item.final_price) / 100 :
                     item.price != null ? Number(item.price) / 100 : null,
      product_quantity: Number(item.quantity || 1),
      product_category: item.product_type || null,
      properties: { cart_mutation_confirmed: "ajax_response" }
    };
  }

  function clearPendingATC() {
    storageRemove(sessionStorage, SK.pendingFormATC);
  }

  function emitAddResponse(data, onConfirmed) {
    // An AJAX add was confirmed: the form-submit fallback must not fire again.
    clearPendingATC();
    if (!data) return;
    const items = Array.isArray(data.items) ? data.items : [data];
    items.forEach((item) => {
      const payload = normalizeAddPayload(item);
      if (payload) onConfirmed(payload);
    });
  }

  function urlString(input) {
    if (typeof input === "string") return input;
    if (input && typeof input.href === "string") return input.href;
    if (input && typeof input.url === "string") return input.url;
    return "";
  }

  function isCartAddUrl(url) {
    return /\/cart\/add(\.js|\.json)?(\?|$|#)/.test(url);
  }

  function watchAddToCart(onConfirmed) {
    if (typeof window.fetch === "function") {
      const originalFetch = window.fetch.bind(window);
      window.fetch = function (input, init) {
        const url = urlString(input);
        const promise = originalFetch(input, init);
        if (isCartAddUrl(url)) {
          promise.then((res) => {
            if (!res || !res.ok) return;
            res.clone().json().then((data) => emitAddResponse(data, onConfirmed)).catch(() => {});
          }).catch(() => {});
        }
        return promise;
      };
    }

    if (typeof window.XMLHttpRequest === "function") {
      const XHR = window.XMLHttpRequest;
      const originalOpen = XHR.prototype.open;
      const originalSend = XHR.prototype.send;

      XHR.prototype.open = function (method, url) {
        this.__noroCartAdd = isCartAddUrl(urlString(url));
        return originalOpen.apply(this, arguments);
      };

      XHR.prototype.send = function () {
        if (this.__noroCartAdd) {
          this.addEventListener("load", function () {
            if (this.status < 200 || this.status >= 300) return;
            emitAddResponse(safeJSONParse(this.responseText, null), onConfirmed);
          });
        }
        return originalSend.apply(this, arguments);
      };
    }

    // Traditional (non-AJAX) cart/add form fallback.
    document.addEventListener("submit", function (event) {
      const form = event.target;
      if (!form || !form.action || !isCartAddUrl(urlString(form.action))) return;

      try {
        const fd = new FormData(form);
        const variantId = fd.get("id");
        if (variantId == null) return;
        storageSet(sessionStorage, SK.pendingFormATC, JSON.stringify({
          variant_id: String(variantId),
          quantity: Number(fd.get("quantity") || 1),
          ts: Date.now()
        }));
      } catch (_) { return; }

      // After all handlers ran: if the theme cancelled the native submit it will
      // add via AJAX, which the fetch/XHR watcher already captures.
      setTimeout(function () {
        if (event.defaultPrevented) clearPendingATC();
      }, 0);
    }, true);
  }

  async function resolvePendingTraditionalATC(onConfirmed) {
    const raw = storageGet(sessionStorage, SK.pendingFormATC);
    if (!raw) return;
    clearPendingATC();

    const pending = safeJSONParse(raw, null);
    if (!pending || !pending.variant_id || Date.now() - pending.ts > PENDING_ATC_MAX_AGE_MS) return;

    try {
      const res = await fetch("/cart.js", { credentials: "same-origin" });
      if (!res.ok) return;
      const cart = await res.json();
      const item = Array.isArray(cart.items)
        ? cart.items.find((x) => String(x.variant_id || x.id) === String(pending.variant_id))
        : null;
      if (!item) return;

      const payload = normalizeAddPayload(item);
      if (!payload) return;
      payload.product_quantity = Number(pending.quantity || 1);
      payload.properties = { cart_mutation_confirmed: "post_redirect_presence" };
      onConfirmed(payload);
    } catch (_) {}
  }

  /* ------------------------------------------------------------------ */
  /* Checkout intent (storefront). Confirmed checkout comes from pixel.  */
  /* ------------------------------------------------------------------ */

  function watchBeginCheckout(onIntent) {
    let last = 0;

    function emit(trigger) {
      const now = Date.now();
      if (now - last < BEGIN_CHECKOUT_DEDUP_MS) return;
      last = now;
      onIntent({ checkout_state: "intent", trigger });
    }

    document.addEventListener("submit", function (event) {
      const form = event.target;
      if (form && form.action && urlString(form.action).includes("/checkout")) emit("form_submit");
    }, true);

    document.addEventListener("click", function (event) {
      const target = event.target && event.target.closest
        ? event.target.closest('a[href*="/checkout"],[data-checkout],[name="checkout"],.cart__checkout,.checkout-button')
        : null;
      if (target) emit("checkout_click");
    }, true);
  }

  /* ------------------------------------------------------------------ */
  /* Tracker                                                             */
  /* ------------------------------------------------------------------ */

  function createTracker(config) {
    const visitor = createVisitor();
    const session = createSession();
    const consent = createConsent(config);
    const clickIds = createClickIds(consent, config);
    const attribution = createAttribution(clickIds);
    const ga4 = config.modules && config.modules.ga4 ? createGA4Adapter(config) : null;
    const queue = createQueue((event) => postEvents(config, [event]));

    attribution.update();
    clickIds.capture();

    function context() {
      const consentState = consent.getState();
      return {
        visitor_id: visitor.getVisitorId(),
        session_id: session.touch(),
        touch: attribution.getCurrentTouch() || {},
        ga_client_id: getGoogleClientId(),
        ga_session_id: getGoogleSessionId(config.ga4MeasurementId),
        ads: clickIds.get(),
        consent: consentState
      };
    }

    async function fire(eventName, payload, eventId) {
      if (!VALID_EVENTS.includes(eventName)) return null;
      if (!consent.isGranted("tracking")) return null;

      const ctx = context();
      const id = eventId || uuid();
      const event = buildEvent(eventName, id, payload || {}, ctx, config);

      if (config.modules && config.modules.noro) {
        const result = await postEvents(config, [event]);
        if (!result.ok && isRetryable(result)) queue.enqueue(event);
      }

      if (ga4 && consent.isGranted("analytics")) {
        ga4.sendEvent(eventName, payload || {});
      }

      return id;
    }

    function bridgeCart(force) {
      if (!config.shopify || !config.shopify.bridge) return;
      updateCartAttributes(buildCartAttributes(context()), !!force);
    }

    consent.onChange(function (state) {
      if (state.advertising) clickIds.promoteHeld();
      else clickIds.purge();
      bridgeCart(true);
    });

    function setup() {
      queue.flush();

      fire("page_view", {
        properties: { page_type: window.__noroPageType || "other" }
      });

      bridgeCart(false);

      if (window.__noroPageType === "product" && window.__noroProductCtx) {
        const p = window.__noroProductCtx;
        fire("view_item", {
          product_id: p.product_id || null,
          variant_id: p.variant_id || null,
          sku: p.product_sku || null,
          product_name: p.product_name || null,
          product_price: p.product_price != null ? Number(p.product_price) : null,
          product_quantity: 1,
          product_category: p.product_category || null
        });
      }

      const onConfirmedATC = (payload) => {
        fire("add_to_cart", payload);
        bridgeCart(true);
      };

      watchAddToCart(onConfirmedATC);
      resolvePendingTraditionalATC(onConfirmedATC);

      watchBeginCheckout((info) => {
        bridgeCart(true);
        fire("begin_checkout", {
          properties: { checkout_state: info.checkout_state, trigger: info.trigger }
        });
      });

      window.addEventListener("focus", () => queue.flush());
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") queue.flush();
      });
    }

    return {
      version: VERSION,
      setup,
      fire,
      track: fire,
      flushQueue: () => queue.flush(),
      getConsent: () => consent.getState(),
      _attribution: attribution
    };
  }

  if (window.__NORO_CONFIG__) {
    const tracker = createTracker(window.__NORO_CONFIG__);
    window.NoroTracker = tracker;
    const init = () => tracker.setup();
    document.readyState === "loading"
      ? document.addEventListener("DOMContentLoaded", init, { once: true })
      : init();
  }
})();
