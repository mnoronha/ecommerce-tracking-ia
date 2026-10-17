/*
  Noro Shopify Custom Web Pixel v2.1
  Client: Enutri
  2026-10-16

  Purpose (observation evidence only — Shopify/Core webhook remains Business Truth):
  - checkout_started   -> begin_checkout (checkout_state: confirmed)
  - checkout_completed -> purchase       (order created; NOT necessarily paid — PIX/boleto)

  Install:
  Shopify Admin -> Settings -> Customer events -> Add custom pixel
  Paste this file, replace __CLIENT_INGEST_TOKEN__ and Connect.

  v2.1 changes vs v2:
  - ga_session_id read from checkout attribute _ga_sid (tracker v4.3.1+)

  v2 changes vs v1:
  - identity read from checkout.attributes (_etv/_noro_sid/_utm_*) first, cookies/localStorage as fallback
  - session read from localStorage (_noro_s/_noro_se), not cookie
  - order.name fallback removed (order number != order id; would break dedup)
  - line items read from CheckoutLineItem.variant (v1 used CartLine.merchandise — wrong shape)
  - checkout.token sent as join key with the order webhook (order.checkout_token)
  - deterministic event IDs: begin_checkout per checkout token, purchase per order id
  - value breakdown (subtotal, shipping, tax, discounts) for later Google/Meta value rules
  - consent: analytics gate + marketing gate for click IDs; live updates via subscribe
  - one retry on network failure

  Backend CORS note:
  This pixel runs inside Shopify's sandboxed iframe, so the request Origin is NOT the
  storefront domain. The ingest endpoint must accept Shopify's pixel sandbox origin
  and answer the CORS preflight (Authorization + JSON).

  Do not paste Agency Admin/Platform/Hermes secrets here.
*/

const NORO = {
  version: "pixel-2.1.0",
  client: "enutri-4sph",
  ingestUrl: "https://ecommerce-tracking-ia-production.up.railway.app/agency/v1/clients/enutri-4sph/tracking/events",
  ingestToken: "__CLIENT_INGEST_TOKEN__",
  sessionTtlMs: 30 * 60 * 1000
};

/* ---------------- consent ---------------- */

const privacy = {
  analytics: readInitPrivacy("analyticsProcessingAllowed"),
  marketing: readInitPrivacy("marketingAllowed")
};

function readInitPrivacy(key) {
  try {
    const cp = init && init.customerPrivacy;
    if (!cp || typeof cp[key] !== "boolean") return null;
    return cp[key];
  } catch (_) {
    return null;
  }
}

try {
  customerPrivacy.subscribe("visitorConsentCollected", (event) => {
    const cp = (event && event.customerPrivacy) || {};
    if (typeof cp.analyticsProcessingAllowed === "boolean") privacy.analytics = cp.analyticsProcessingAllowed;
    if (typeof cp.marketingAllowed === "boolean") privacy.marketing = cp.marketingAllowed;
  });
} catch (_) {}

function analyticsAllowed() {
  // null = no regional consent requirement reported by Shopify.
  return privacy.analytics !== false;
}

function marketingAllowed() {
  // Click IDs only exist in checkout.attributes when the storefront already had
  // advertising consent; here we only block an explicit denial during checkout.
  return privacy.marketing !== false;
}

/* ---------------- helpers ---------------- */

function numericId(value) {
  if (value == null) return null;
  const m = String(value).match(/(\d+)\s*$/);
  return m ? m[1] : null;
}

function stableFallbackHash(input) {
  let h1 = 0x811c9dc5;
  let h2 = 0x01000193;
  for (let i = 0; i < input.length; i++) {
    h1 ^= input.charCodeAt(i);
    h1 = Math.imul(h1, 16777619);
    h2 ^= input.charCodeAt(i);
    h2 = Math.imul(h2, 2246822519);
  }
  return "noro_" +
    (h1 >>> 0).toString(16).padStart(8, "0") +
    (h2 >>> 0).toString(16).padStart(8, "0");
}

async function sha256(input) {
  try {
    if (typeof crypto !== "undefined" && crypto.subtle && typeof TextEncoder !== "undefined") {
      const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(input));
      return Array.from(new Uint8Array(digest))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
    }
  } catch (_) {}
  return stableFallbackHash(input);
}

function money(m) {
  if (!m || m.amount == null) return null;
  const n = Number(m.amount);
  return Number.isFinite(n) ? n : null;
}

function attributesMap(list) {
  const out = {};
  if (Array.isArray(list)) {
    list.forEach((a) => {
      if (a && a.key) out[a.key] = a.value != null ? String(a.value) : "";
    });
  }
  return out;
}

async function readCookie(name) {
  try { return (await browser.cookie.get(name)) || null; } catch (_) { return null; }
}

async function readLocal(key) {
  try { return (await browser.localStorage.getItem(key)) || null; } catch (_) { return null; }
}

async function readSession() {
  const id = await readLocal("_noro_s");
  if (!id) return { id: null, expired: null };
  const exp = parseInt((await readLocal("_noro_se")) || "0", 10);
  return { id, expired: !(exp > Date.now()) };
}

function safePath(event) {
  try {
    const loc = event.context && event.context.document && event.context.document.location;
    return loc && loc.pathname ? loc.pathname : null;
  } catch (_) {
    return null;
  }
}

function lineItem(line) {
  if (!line) return null;
  const variant = line.variant || {};
  const product = variant.product || {};
  const quantity = Number(line.quantity || 1);
  const lineTotal = money(line.finalLinePrice);

  return {
    product_id: numericId(product.id),
    variant_id: numericId(variant.id),
    sku: variant.sku || null,
    product_name: product.title || line.title || null,
    variant_name: variant.title || null,
    product_category: product.type || null,
    price: money(variant.price),
    quantity,
    line_total: lineTotal
  };
}

async function sendEvent(event) {
  const body = JSON.stringify({ events: [event] });
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const res = await fetch(NORO.ingestUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": "Bearer " + NORO.ingestToken
        },
        body,
        keepalive: true
      });
      if (res.ok || (res.status >= 400 && res.status < 500 && res.status !== 408 && res.status !== 429)) return;
    } catch (_) {}
    await new Promise((r) => setTimeout(r, 1500));
  }
}

/* ---------------- shared event builder ---------------- */

async function buildBase(event, checkout) {
  const attrs = attributesMap(checkout.attributes);
  const session = await readSession();
  const visitorId = attrs._etv || (await readCookie("_noro_v")) || null;
  const sessionId = attrs._noro_sid || session.id || null;
  const storefrontMarketing = !attrs._noro_consent || /m1$/.test(attrs._noro_consent);
  const marketing = marketingAllowed() && storefrontMarketing;

  return {
    occurred_at: event.timestamp || new Date().toISOString(),
    visitor_cookie_id: visitorId,
    session_id: sessionId,
    url: safePath(event),
    referrer: null,

    utm_source: attrs._utm_source || null,
    utm_medium: attrs._utm_medium || null,
    utm_campaign: attrs._utm_campaign || null,
    utm_content: attrs._utm_content || null,
    utm_term: attrs._utm_term || null,

    gclid: marketing ? attrs._gclid || null : null,
    gbraid: marketing ? attrs._gbraid || null : null,
    wbraid: marketing ? attrs._wbraid || null : null,
    fbclid: marketing ? attrs._fbclid || null : null,
    fbp: marketing ? attrs._fbp || null : null,
    fbc: marketing ? attrs._fbc || null : null,
    ga_client_id: attrs._gcid || null,
    ga_session_id: attrs._ga_sid || null,

    currency: checkout.currencyCode || null,

    _identity: {
      visitor_source: attrs._etv ? "checkout_attributes" : (visitorId ? "cookie" : "none"),
      session_source: attrs._noro_sid ? "checkout_attributes" : (session.id ? "local_storage" : "none"),
      session_expired: attrs._noro_sid ? null : session.expired,
      click_source: attrs._noro_click_src || null,
      consent: {
        analytics: privacy.analytics,
        marketing: privacy.marketing,
        storefront: attrs._noro_consent || null
      }
    }
  };
}

function valueBreakdown(checkout) {
  return {
    total: money(checkout.totalPrice),
    subtotal: money(checkout.subtotalPrice),
    shipping: checkout.shippingLine ? money(checkout.shippingLine.price) : null,
    tax: money(checkout.totalTax),
    discounts: money(checkout.discountsAmount)
  };
}

/* ---------------- checkout_started ---------------- */

analytics.subscribe("checkout_started", async (event) => {
  if (!analyticsAllowed()) return;
  const checkout = event.data && event.data.checkout;
  if (!checkout) return;

  const base = await buildBase(event, checkout);
  const identity = base._identity;
  delete base._identity;

  const token = checkout.token || null;
  const eventId = token
    ? await sha256("begin_checkout_noro_" + token)
    : (event.id || stableFallbackHash("checkout_started:" + event.timestamp));

  const values = valueBreakdown(checkout);

  await sendEvent(Object.assign(base, {
    event_name: "begin_checkout",
    event_id: eventId,
    value: values.total,
    properties: {
      checkout_state: "confirmed",
      checkout_token: token,
      source: "shopify_web_pixel",
      pixel_version: NORO.version,
      business_truth: false,
      values,
      items: Array.isArray(checkout.lineItems) ? checkout.lineItems.map(lineItem).filter(Boolean) : [],
      identity
    }
  }));
});

/* ---------------- checkout_completed ---------------- */

analytics.subscribe("checkout_completed", async (event) => {
  if (!analyticsAllowed()) return;
  const checkout = event.data && event.data.checkout;
  if (!checkout) return;

  // Order ID only. Never fall back to order.name (#1001 is the order number, not the id).
  const orderId = numericId(checkout.order && checkout.order.id);
  if (!orderId) return; // server reconciliation will capture it

  const base = await buildBase(event, checkout);
  const identity = base._identity;
  delete base._identity;

  const values = valueBreakdown(checkout);
  let isFirstOrder = null;
  try {
    const c = checkout.order && checkout.order.customer;
    if (c && typeof c.isFirstOrder === "boolean") isFirstOrder = c.isFirstOrder;
  } catch (_) {}

  await sendEvent(Object.assign(base, {
    event_name: "purchase",
    event_id: await sha256("purchase_noro_" + orderId),
    order_id: orderId,
    value: values.total,
    properties: {
      checkout_token: checkout.token || null,
      source: "shopify_web_pixel",
      pixel_version: NORO.version,
      business_truth: false,
      payment_confirmed: false,
      is_first_order: isFirstOrder,
      values,
      items: Array.isArray(checkout.lineItems) ? checkout.lineItems.map(lineItem).filter(Boolean) : [],
      identity
    }
  }));
});
