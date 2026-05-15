/**
 * Ecommerce Tracking Pixel — tracker.js  v2.3.0
 *
 * Usage A — HTML attribute (manual install):
 *   <script
 *     src="https://api.example.com/static/tracker.js"
 *     data-client-id="YOUR_CLIENT_ID"
 *     async
 *   ></script>
 *
 * Usage B — Shopify ScriptTag API (auto-install, no theme editing):
 *   src: https://api.example.com/static/tracker.js?client_id=YOUR_CLIENT_ID
 *   (api-url is auto-detected from the script's own origin)
 *
 * Usage C — programmatic config before the tag:
 *   <script>window.__ETConfig = { clientId: "...", apiUrl: "..." };</script>
 */
(function (w, d) {
  'use strict';

  // ── Constants ──────────────────────────────────────────────────────────────
  var COOKIE_VISITOR    = '_etv';   // visitor ID  — 1st-party, 1 year
  var COOKIE_ATTR       = '_eta';   // UTM attribution — 30 days
  var COOKIE_GCLID      = '_etg';   // Google click ID — 90 days
  var COOKIE_FBP        = '_fbp';   // Meta browser ID — 90 days (Meta standard)
  var COOKIE_FBC        = '_fbc';   // Meta click ID  — 90 days (Meta standard)
  var COOKIE_TTCLID     = '_ettc';  // TikTok click ID — 90 days
  var STORAGE_SESSION   = '_ets';   // session ID — sessionStorage (tab lifetime)
  var VISITOR_TTL_DAYS  = 365;
  var ATTR_TTL_DAYS     = 30;
  var AD_ID_TTL_DAYS    = 90;

  // ── Bootstrap config ──────────────────────────────────────────────────────
  var cfg    = w.__ETConfig || {};
  var script = d.currentScript || (function () {
    // Fallback for async-deferred scripts
    var scripts = d.getElementsByTagName('script');
    return scripts[scripts.length - 1];
  })();

  // Read a param from the script's own src URL.
  // Enables Shopify ScriptTag usage: tracker.js?client_id=xxx
  function _srcParam(name) {
    try {
      if (script && script.src) {
        return new URL(script.src).searchParams.get(name);
      }
    } catch (e) {}
    return null;
  }

  // When loaded via ScriptTag API the api_url isn't set — infer from script origin
  function _scriptOrigin() {
    try {
      if (script && script.src) return new URL(script.src).origin;
    } catch (e) {}
    return '';
  }

  var CLIENT_ID = cfg.clientId
    || (script && script.getAttribute('data-client-id'))
    || _srcParam('client_id')
    || '';

  var API_URL = (
    cfg.apiUrl
    || (script && script.getAttribute('data-api-url'))
    || _srcParam('api_url')
    || _scriptOrigin()
  ).replace(/\/$/, '');

  // ── Cookie utilities ───────────────────────────────────────────────────────
  function setCookie(name, value, days) {
    var expires = '';
    if (days) {
      var d = new Date();
      d.setTime(d.getTime() + days * 864e5);
      expires = '; expires=' + d.toUTCString();
    }
    // Attempt domain-wide scope so subdomains share the same visitor ID
    var domain = '';
    try {
      var parts = location.hostname.split('.');
      if (parts.length >= 2) {
        domain = '; domain=.' + parts.slice(-2).join('.');
      }
    } catch (e) { /* ignore */ }
    document.cookie =
      name + '=' + encodeURIComponent(value) +
      expires + '; path=/' + domain + '; SameSite=Lax';
  }

  function getCookie(name) {
    var re    = new RegExp('(?:^|;\\s*)' + name + '=([^;]*)');
    var match = document.cookie.match(re);
    return match ? decodeURIComponent(match[1]) : null;
  }

  // ── UUID v4 generator ─────────────────────────────────────────────────────
  function uuid() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  // ── Visitor ID — 1st-party cookie, 1 year ─────────────────────────────────
  function getVisitorId() {
    var vid = getCookie(COOKIE_VISITOR);
    if (!vid) {
      vid = uuid();
      setCookie(COOKIE_VISITOR, vid, VISITOR_TTL_DAYS);
    }
    return vid;
  }

  // ── Session ID — sessionStorage, refreshed per tab ────────────────────────
  function getSessionId() {
    try {
      var sid = sessionStorage.getItem(STORAGE_SESSION);
      if (!sid) {
        sid = uuid();
        sessionStorage.setItem(STORAGE_SESSION, sid);
      }
      return sid;
    } catch (e) {
      return uuid(); // Private-mode fallback
    }
  }

  // ── UTM attribution — 30-day cookie, last-touch model ─────────────────────
  function parseCurrentUTMs() {
    var result = {};
    var search = location.search;
    ['source', 'medium', 'campaign', 'term', 'content'].forEach(function (k) {
      var re    = new RegExp('[?&]utm_' + k + '=([^&#]*)');
      var match = search.match(re);
      if (match) result[k] = decodeURIComponent(match[1].replace(/\+/g, ' '));
    });
    return Object.keys(result).length ? result : null;
  }

  function getAttribution() {
    var fresh = parseCurrentUTMs();
    if (fresh) {
      setCookie(COOKIE_ATTR, JSON.stringify(fresh), ATTR_TTL_DAYS);
      return fresh;
    }
    var stored = getCookie(COOKIE_ATTR);
    if (stored) {
      try { return JSON.parse(stored); } catch (e) { /* malformed */ }
    }
    return null;
  }

  // ── Advertising identifiers ───────────────────────────────────────────────
  function getQueryParam(name) {
    var re = new RegExp('[?&]' + name + '=([^&#]*)', 'i');
    var match = location.search.match(re);
    return match ? decodeURIComponent(match[1].replace(/\+/g, ' ')) : null;
  }

  // gclid — Google click ID (URL param, persisted 90 days)
  function getGclid() {
    var fresh = getQueryParam('gclid');
    if (fresh) {
      setCookie(COOKIE_GCLID, fresh, AD_ID_TTL_DAYS);
      return fresh;
    }
    return getCookie(COOKIE_GCLID) || null;
  }

  // _fbp — Meta browser ID. If Meta Pixel is on the page, the cookie already exists;
  // otherwise we generate one in Meta's documented format.
  function getFbp() {
    var fbp = getCookie(COOKIE_FBP);
    if (fbp) return fbp;
    // Meta format: fb.1.{timestamp_ms}.{random_int}
    var rand = Math.floor(Math.random() * 1e16).toString();
    fbp = 'fb.1.' + Date.now() + '.' + rand;
    setCookie(COOKIE_FBP, fbp, AD_ID_TTL_DAYS);
    return fbp;
  }

  // _fbc — Meta click ID. Built from ?fbclid= and persisted 90 days.
  function getFbc() {
    var fresh = getQueryParam('fbclid');
    if (fresh) {
      var fbc = 'fb.1.' + Date.now() + '.' + fresh;
      setCookie(COOKIE_FBC, fbc, AD_ID_TTL_DAYS);
      return fbc;
    }
    return getCookie(COOKIE_FBC) || null;
  }

  // ttclid — TikTok click ID (URL param, persisted 90 days)
  function getTtclid() {
    var fresh = getQueryParam('ttclid');
    if (fresh) {
      setCookie(COOKIE_TTCLID, fresh, AD_ID_TTL_DAYS);
      return fresh;
    }
    return getCookie(COOKIE_TTCLID) || null;
  }

  // GA4 client_id — extracted from the _ga cookie set by gtag.js / GA4
  // Format of _ga: GA1.{n}.{client_id_part1}.{client_id_part2}
  function getGaClientId() {
    var ga = getCookie('_ga');
    if (!ga) return null;
    var parts = ga.split('.');
    if (parts.length < 4) return null;
    return parts[2] + '.' + parts[3];
  }

  // ── Payload builder ───────────────────────────────────────────────────────
  function buildPayload(eventType, extra) {
    return {
      client_id:    CLIENT_ID,
      event_type:   eventType,
      visitor_id:   getVisitorId(),
      session_id:   getSessionId(),
      page_url:     location.href,
      referrer:     document.referrer || null,
      utm:          getAttribution(),
      timestamp:    new Date().toISOString(),
      fbp:          getFbp(),
      fbc:          getFbc(),
      ga_client_id: getGaClientId(),
      gclid:        getGclid(),
      ttclid:       getTtclid(),
      metadata:     extra || {}
    };
  }

  // ── Transport — Beacon → fetch → img-pixel fallback ──────────────────────
  function sendBeacon(url, payload) {
    if (!navigator.sendBeacon) return false;
    try {
      return navigator.sendBeacon(
        url,
        new Blob([JSON.stringify(payload)], { type: 'application/json' })
      );
    } catch (e) { return false; }
  }

  function sendFetch(url, payload) {
    try {
      fetch(url, {
        method:    'POST',
        headers:   { 'Content-Type': 'application/json' },
        body:      JSON.stringify(payload),
        keepalive: true
      }).catch(function () {});
      return true;
    } catch (e) { return false; }
  }

  function sendPixelImg(eventType, payload) {
    try {
      var qs = [
        'cid=' + encodeURIComponent(CLIENT_ID),
        'et='  + encodeURIComponent(eventType),
        'vid=' + encodeURIComponent(payload.visitor_id || ''),
        'url=' + encodeURIComponent(payload.page_url  || ''),
        'ref=' + encodeURIComponent(payload.referrer  || '')
      ].join('&');
      var img   = new Image(1, 1);
      img.style.display = 'none';
      img.src   = API_URL + '/pixel/events?' + qs;
      // Attach to DOM so the request fires even in some strict browsers
      var body  = document.body;
      if (body) {
        body.appendChild(img);
        img.onload = img.onerror = function () { body.removeChild(img); };
      }
    } catch (e) { /* best effort */ }
  }

  // ── Public track function ─────────────────────────────────────────────────
  function track(eventType, data) {
    if (!CLIENT_ID || !API_URL) {
      if (w.console && console.warn) {
        console.warn('[ET] tracker not configured: set data-client-id and data-api-url');
      }
      return;
    }
    var endpoint = API_URL + '/pixel/events';
    var payload  = buildPayload(eventType, data);

    if (!sendBeacon(endpoint, payload)) {
      if (!sendFetch(endpoint, payload)) {
        sendPixelImg(eventType, payload);
      }
    }
  }

  // ── Auto-track pageview ───────────────────────────────────────────────────
  function trackPageview() {
    track('pageview');
  }

  // ── SPA support — intercept history.pushState ──────────────────────────────
  (function patchHistory() {
    var original = history.pushState;
    history.pushState = function () {
      original.apply(history, arguments);
      // Fire after the URL has changed
      w.setTimeout(trackPageview, 0);
    };
    w.addEventListener('popstate', trackPageview);
  })();

  // ── Post-purchase attribution survey ──────────────────────────────────────
  // Renders a small "how did you hear about us?" modal on the thank-you page,
  // POSTs the answer to /journey/<client>/survey-response. Crossed with our
  // UTMs/click IDs in the dashboard, this rescues attribution for the bulk of
  // orders that arrive without a UTM (dark social, influencers, word of mouth).

  var SURVEY_STORAGE_KEY = '_etsurvey';   // localStorage: orders already answered

  var SURVEY_OPTIONS = [
    { key: 'meta',            label: 'Instagram / Facebook' },
    { key: 'google',          label: 'Google (busca ou anúncio)' },
    { key: 'tiktok',          label: 'TikTok' },
    { key: 'youtube',         label: 'YouTube' },
    { key: 'influencer',      label: 'Indicação de influenciador' },
    { key: 'referral_friend', label: 'Indicação de amigo / família' },
    { key: 'organic_search',  label: 'Busca orgânica' },
    { key: 'email',           label: 'E-mail' },
    { key: 'podcast',         label: 'Podcast' },
    { key: 'event_offline',   label: 'Evento / loja física' },
    { key: 'other',           label: 'Outro' }
  ];

  function isThankYouPage() {
    // Shopify: /checkouts/<token>/thank_you or /orders/<id>
    // Generic: /thank-you, /obrigado, /pedido-confirmado
    var p = (location.pathname || '').toLowerCase();
    return /\/(thank[-_ ]?you|obrigado|orders\/[^/]+|pedido-confirmado|order-confirmation)/.test(p);
  }

  function extractOrderId() {
    // Best effort: pull from URL path /orders/<id> or query ?order_id=
    var m = (location.pathname || '').match(/\/orders\/([^/?#]+)/i);
    if (m && m[1]) return m[1];
    var q = getQueryParam('order_id') || getQueryParam('order');
    if (q) return q;
    // Shopify exposes a global on thank-you pages
    try {
      if (w.Shopify && w.Shopify.checkout && w.Shopify.checkout.order_id) {
        return String(w.Shopify.checkout.order_id);
      }
    } catch (e) { /* ignore */ }
    return null;
  }

  function alreadyAnswered(orderId) {
    try {
      var stored = localStorage.getItem(SURVEY_STORAGE_KEY);
      if (!stored) return false;
      var ids = JSON.parse(stored);
      return Array.isArray(ids) && ids.indexOf(orderId) >= 0;
    } catch (e) { return false; }
  }

  function markAnswered(orderId) {
    try {
      var stored = localStorage.getItem(SURVEY_STORAGE_KEY);
      var ids = stored ? JSON.parse(stored) : [];
      if (!Array.isArray(ids)) ids = [];
      if (ids.indexOf(orderId) < 0) ids.push(orderId);
      // Keep only last 50 to avoid unbounded growth
      if (ids.length > 50) ids = ids.slice(-50);
      localStorage.setItem(SURVEY_STORAGE_KEY, JSON.stringify(ids));
    } catch (e) { /* ignore */ }
  }

  function submitSurvey(sourceKey, freeText, orderId) {
    if (!CLIENT_ID || !API_URL) return;
    var url = API_URL + '/journey/' + encodeURIComponent(CLIENT_ID) + '/survey-response';
    var payload = {
      source_declared:   sourceKey,
      free_text:         freeText || null,
      order_id:          orderId || null,
      visitor_cookie_id: getVisitorId(),
      page_url:          location.href
    };
    try {
      fetch(url, {
        method:    'POST',
        headers:   { 'Content-Type': 'application/json' },
        body:      JSON.stringify(payload),
        keepalive: true
      }).catch(function () {});
    } catch (e) { /* ignore */ }
  }

  function renderSurveyModal(orderId) {
    if (!d.body) return;
    if (d.getElementById('_etsurvey-root')) return;

    var root = d.createElement('div');
    root.id = '_etsurvey-root';
    root.style.cssText = [
      'position:fixed','inset:0','z-index:2147483646',
      'display:flex','align-items:flex-end','justify-content:center',
      'pointer-events:none'
    ].join(';');

    var card = d.createElement('div');
    card.style.cssText = [
      'pointer-events:auto',
      'background:#0f1117','color:#e5e7eb',
      'border:1px solid #2a2f3e','border-radius:14px 14px 0 0',
      'box-shadow:0 -8px 32px rgba(0,0,0,.4)',
      'width:min(420px,92vw)','padding:18px 18px 16px',
      'font:14px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',
      'margin-bottom:0'
    ].join(';');

    var close = d.createElement('button');
    close.textContent = '×';
    close.setAttribute('aria-label', 'Fechar');
    close.style.cssText = [
      'position:absolute','top:8px','right:12px',
      'background:transparent','border:0','color:#94a3b8',
      'font-size:22px','cursor:pointer','line-height:1'
    ].join(';');
    close.onclick = function () { d.body.removeChild(root); };
    card.style.position = 'relative';
    card.appendChild(close);

    var title = d.createElement('div');
    title.textContent = 'Antes de você ir — como você nos conheceu?';
    title.style.cssText = 'font-weight:600;font-size:15px;margin-bottom:4px;color:#fff';
    card.appendChild(title);

    var subtitle = d.createElement('div');
    subtitle.textContent = 'Sua resposta nos ajuda a melhorar a experiência. Leva 2 segundos.';
    subtitle.style.cssText = 'font-size:12px;color:#94a3b8;margin-bottom:12px';
    card.appendChild(subtitle);

    var grid = d.createElement('div');
    grid.style.cssText = 'display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px';

    SURVEY_OPTIONS.forEach(function (opt) {
      var btn = d.createElement('button');
      btn.type = 'button';
      btn.textContent = opt.label;
      btn.style.cssText = [
        'background:#1a1f2e','color:#e5e7eb',
        'border:1px solid #2a2f3e','border-radius:8px',
        'padding:9px 10px','font-size:12.5px','cursor:pointer',
        'text-align:left','transition:background .15s,border-color .15s'
      ].join(';');
      btn.onmouseover = function () { btn.style.background = '#252a3a'; btn.style.borderColor = '#3a4055'; };
      btn.onmouseout  = function () { btn.style.background = '#1a1f2e'; btn.style.borderColor = '#2a2f3e'; };
      btn.onclick = function () {
        submitSurvey(opt.key, null, orderId);
        if (orderId) markAnswered(orderId);
        // Replace modal contents with a thank-you confirmation that fades.
        card.innerHTML = '';
        var ok = d.createElement('div');
        ok.textContent = 'Obrigado pelo feedback!';
        ok.style.cssText = 'font-size:14px;color:#34d399;padding:6px 0;text-align:center;font-weight:600';
        card.appendChild(ok);
        w.setTimeout(function () {
          try { d.body.removeChild(root); } catch (e) { /* ignore */ }
        }, 1500);
      };
      grid.appendChild(btn);
    });
    card.appendChild(grid);

    root.appendChild(card);
    d.body.appendChild(root);
  }

  function maybeShowSurvey() {
    if (!isThankYouPage()) return;
    var orderId = extractOrderId() || 'no-order-' + Date.now();
    if (alreadyAnswered(orderId)) return;
    // Small delay so the page settles first (and conversion pixels fire first).
    w.setTimeout(function () { renderSurveyModal(orderId); }, 1200);
  }

  // ── Shopify cart attribute injection ──────────────────────────────────────
  // Writes visitor/ad identifiers as Shopify cart note_attributes so that the
  // orders/paid webhook carries fbp, fbc, gclid, and visitor cookie ID.
  // Without this, server-side attribution falls back to email-only matching,
  // which only works for ~0.1% of visitors who have a prior order.
  // Keys prefixed with _ are hidden from the merchant UI but forwarded on webhooks.

  var _CART_INJECT_KEY = '_etci'; // sessionStorage flag: already injected this session

  function injectShopifyCartAttributes() {
    if (!w.Shopify) return; // only on Shopify storefronts
    try {
      if (sessionStorage.getItem(_CART_INJECT_KEY)) return;
      sessionStorage.setItem(_CART_INJECT_KEY, '1');
    } catch (e) { /* private mode — proceed anyway */ }

    var attrs = { '_etv': getVisitorId() };
    var fbp    = getFbp();
    var fbc    = getFbc();
    var gclid  = getGclid();
    var gcid   = getGaClientId();
    var ttclid = getTtclid();

    if (fbp)    attrs['_fbp']   = fbp;
    if (fbc)    attrs['_fbc']   = fbc;
    if (gclid)  attrs['_gclid'] = gclid;
    if (gcid)   attrs['_gcid']  = gcid;
    if (ttclid) attrs['_ettc']  = ttclid;

    try {
      fetch('/cart/update.js', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ attributes: attrs }),
        keepalive: true
      }).catch(function () {});
    } catch (e) { /* ignore */ }
  }

  // ── Public API ────────────────────────────────────────────────────────────
  w.ET = {
    track:           track,
    getVisitorId:    getVisitorId,
    getSessionId:    getSessionId,
    getAttribution:  getAttribution,
    getFbp:          getFbp,
    getFbc:          getFbc,
    getGclid:        getGclid,
    getGaClientId:   getGaClientId,
    showSurvey:      renderSurveyModal,
    injectCartAttrs: injectShopifyCartAttributes
  };

  // ── Fire initial pageview + cart injection ────────────────────────────────
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      trackPageview();
      maybeShowSurvey();
      injectShopifyCartAttributes();
    });
  } else {
    trackPageview();
    maybeShowSurvey();
    injectShopifyCartAttributes();
  }

}(window, document));
