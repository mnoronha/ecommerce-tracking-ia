/**
 * Ecommerce Tracking Pixel — tracker.js  v2.1.0
 *
 * Usage:
 *   <script
 *     src="/pixel/tracker.js"
 *     data-client-id="YOUR_CLIENT_ID"
 *     data-api-url="https://api.yourdomain.com"
 *     async
 *   ></script>
 *
 * Or configure programmatically before the script tag:
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

  var CLIENT_ID = cfg.clientId || (script && script.getAttribute('data-client-id')) || '';
  var API_URL   = (cfg.apiUrl   || (script && script.getAttribute('data-api-url'))   || '').replace(/\/$/, '');

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

  // ── Public API ────────────────────────────────────────────────────────────
  w.ET = {
    track:          track,
    getVisitorId:   getVisitorId,
    getSessionId:   getSessionId,
    getAttribution: getAttribution,
    getFbp:         getFbp,
    getFbc:         getFbc,
    getGclid:       getGclid,
    getGaClientId:  getGaClientId
  };

  // ── Fire initial pageview ─────────────────────────────────────────────────
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', trackPageview);
  } else {
    trackPageview();
  }

}(window, document));
