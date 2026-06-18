import hashlib
import hmac
import base64
import uuid
from datetime import datetime
from typing import Optional, List
from urllib.parse import parse_qs, urlparse

from .base import BaseAdapter
from ...models.events import (
    NormalizedEvent,
    EventType,
    CustomerData,
    OrderData,
    OrderItem,
    Address,
    UTMParams,
)


class ShopifyAdapter(BaseAdapter):
    platform_name = "shopify"

    TOPIC_MAP: dict = {
        "orders/create": EventType.ORDER_CREATED,
        "orders/updated": EventType.ORDER_UPDATED,
        "orders/paid": EventType.ORDER_PAID,
        "orders/cancelled": EventType.ORDER_CANCELLED,
        "orders/fulfilled": EventType.ORDER_FULFILLED,
        "refunds/create": EventType.ORDER_REFUNDED,
        "carts/create": EventType.CART_CREATED,
        "carts/update": EventType.CART_UPDATED,
        "checkouts/create": EventType.CHECKOUT_STARTED,
        "checkouts/update": EventType.CHECKOUT_STARTED,
        "customers/create": EventType.CUSTOMER_CREATED,
    }

    # ------------------------------------------------------------------ #
    # Signature validation                                                 #
    # ------------------------------------------------------------------ #

    def validate_signature(self, payload: bytes, headers: dict, secret: str) -> bool:
        """Validate Shopify HMAC-SHA256 from X-Shopify-Hmac-Sha256 header."""
        shopify_hmac = (
            headers.get("x-shopify-hmac-sha256")
            or headers.get("X-Shopify-Hmac-Sha256")
            or ""
        )
        if not shopify_hmac:
            return False

        digest = hmac.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).digest()
        computed = base64.b64encode(digest).decode("utf-8")
        return hmac.compare_digest(computed, shopify_hmac)

    # ------------------------------------------------------------------ #
    # Parsing helpers                                                      #
    # ------------------------------------------------------------------ #

    # Domains we can map to a coarse source/medium when no UTM is present.
    # Matched against urlparse(referring_site).netloc with leading "www." / "l."
    # / "m." stripped.
    _REFERRER_MAP: dict = {
        "google.com":      ("google",    "organic"),
        "google.com.br":   ("google",    "organic"),
        "bing.com":        ("bing",      "organic"),
        "duckduckgo.com":  ("duckduckgo","organic"),
        "yahoo.com":       ("yahoo",     "organic"),
        "instagram.com":   ("instagram", "social"),
        "facebook.com":    ("facebook",  "social"),
        "fb.com":          ("facebook",  "social"),
        "messenger.com":   ("facebook",  "social"),
        "tiktok.com":      ("tiktok",    "social"),
        "youtube.com":     ("youtube",   "social"),
        "youtu.be":        ("youtube",   "social"),
        "twitter.com":     ("twitter",   "social"),
        "x.com":           ("twitter",   "social"),
        "t.co":            ("twitter",   "social"),
        "linkedin.com":    ("linkedin",  "social"),
        "pinterest.com":   ("pinterest", "social"),
        "whatsapp.com":    ("whatsapp",  "social"),
        "wa.me":           ("whatsapp",  "social"),
        "reddit.com":      ("reddit",    "social"),
        "klaviyo.com":     ("klaviyo",   "email"),
    }

    @staticmethod
    def _normalize_referrer_host(referring_site: str) -> Optional[str]:
        """Strip protocol + common subdomain prefixes for referrer lookup."""
        if not referring_site:
            return None
        try:
            host = urlparse(referring_site).netloc.lower()
        except Exception:
            return None
        if not host:
            return None
        for prefix in ("www.", "l.", "m.", "lm.", "out.", "go.", "link."):
            if host.startswith(prefix):
                host = host[len(prefix):]
                break
        return host

    def _infer_utm(
        self,
        landing_site:   Optional[str],
        referring_site: Optional[str],
        source_name:    Optional[str],
        note_attrs:     Optional[dict] = None,
    ) -> Optional[UTMParams]:
        """
        Best-available attribution for a Shopify order.

        Cascade (highest priority first). Signals tied to the *current*
        landing URL beat cart-attribute leftovers from older sessions:

          1. UTM in landing_site qs                      — explicit, this visit
          2. UTM in note_attrs (`_utm_*`)                — explicit, our cookie (≤30d)
          3. gclid in landing_site                       — google/cpc, this visit
          4. fbclid in landing_site                      — facebook/paid_social, this visit
          5. srsltid in landing_site                     — google/organic (Shopping/Search)
          6. _kx= in landing_site                        — klaviyo/email
          7. gclid in note_attrs                         — google/cpc (older session)
          8. _fbc in note_attrs                          — facebook/paid_social (older session)
          9. referring_site host map                     — e.g. instagram → social
         10. source_name == 'pos'                        — pos/in_store
         11. None (truly unattributed)

        Older clickids are demoted past current-visit signals so a customer
        who arrived today via Google Shopping (srsltid) but has a stale fbc
        cookie from a past Instagram ad is still credited to Google.
        """
        note_attrs = note_attrs or {}

        qs: dict = {}
        if landing_site:
            try:
                qs_raw = parse_qs(urlparse(landing_site).query)
                qs = {k: v[0] for k, v in qs_raw.items() if v}
            except Exception:
                qs = {}

        # 1. Explicit UTM in the landing URL — strongest, current-visit signal
        if any(qs.get(k) for k in ("utm_source", "utm_medium", "utm_campaign")):
            return UTMParams(
                source=qs.get("utm_source"),
                medium=qs.get("utm_medium"),
                campaign=qs.get("utm_campaign"),
                term=qs.get("utm_term"),
                content=qs.get("utm_content"),
            )

        # 2. Pixel-injected UTMs from `_utm_*` cart attributes (≤30d cookie)
        if any(note_attrs.get("_utm_" + k) for k in ("source", "medium", "campaign")):
            return UTMParams(
                source=note_attrs.get("_utm_source"),
                medium=note_attrs.get("_utm_medium"),
                campaign=note_attrs.get("_utm_campaign"),
                term=note_attrs.get("_utm_term"),
                content=note_attrs.get("_utm_content"),
            )

        # 3-4. Current-visit clickids
        if qs.get("gclid"):
            return UTMParams(source="google", medium="cpc")
        if qs.get("fbclid"):
            return UTMParams(source="facebook", medium="paid_social")

        # 5. Google Shopping / Search organic — landing carries srsltid
        if qs.get("srsltid"):
            return UTMParams(source="google", medium="organic")

        # 6. Klaviyo's per-recipient identifier on email links
        if qs.get("_kx"):
            return UTMParams(source="klaviyo", medium="email")

        # 7-8. Older clickids — only when nothing on the current visit matched
        if note_attrs.get("_gclid"):
            return UTMParams(source="google", medium="cpc")
        if note_attrs.get("_fbc"):
            return UTMParams(source="facebook", medium="paid_social")

        # 9. Referring-site host map — covers organic search and unattributed social
        host = self._normalize_referrer_host(referring_site or "")
        if host:
            for domain, (source, medium) in self._REFERRER_MAP.items():
                if host == domain or host.endswith("." + domain):
                    return UTMParams(source=source, medium=medium)

        # 10. POS terminal sale (Shopify Point of Sale) — physical store
        sn = (source_name or "").lower()
        if sn == "pos":
            return UTMParams(source="pos", medium="in_store")

        # 11. Draft order created in Shopify Admin — usually phone/WhatsApp
        # orders the merchant entered by hand. No digital trail to attribute.
        if sn == "shopify_draft_order":
            return UTMParams(source="draft", medium="manual")

        # 12. source_name == "web" with no UTM / referrer / clickids → typed
        # the URL directly or used a saved bookmark. Surface it explicitly so
        # the dashboard distinguishes "direct" from "unknown / not received".
        if sn == "web":
            return UTMParams(source="direct", medium="none")

        return None

    def _parse_address(self, addr: dict) -> Optional[Address]:
        if not addr:
            return None
        return Address(
            street=addr.get("address1"),
            city=addr.get("city"),
            state=addr.get("province"),
            country=addr.get("country"),
            zip_code=addr.get("zip"),
        )

    def _merge_address_fields(self, *addresses: dict) -> Optional[Address]:
        """
        Build an Address by picking each field from the first available source.
        Shopify often populates zip on shipping but not on customer.default_address,
        so per-field fallback prevents losing data that exists on a sibling.
        Critical for Meta EMQ: zip_code adds ~6 points to match quality.
        """
        valid = [a for a in addresses if a]
        if not valid:
            return None

        def _pick(key: str) -> Optional[str]:
            for a in valid:
                v = a.get(key)
                if v:
                    return v
            return None

        return Address(
            street=_pick("address1"),
            city=_pick("city"),
            state=_pick("province"),
            country=_pick("country"),
            zip_code=_pick("zip"),
        )

    def _parse_customer(self, data: dict) -> Optional[CustomerData]:
        customer = data.get("customer") or {}
        if not customer and not data.get("email"):
            return None
        # Shopify provides first_name/last_name separately on customer AND on
        # shipping_address. Customer object wins; fall back to shipping address.
        ship = data.get("shipping_address") or {}
        bill = data.get("billing_address") or {}
        first_name = customer.get("first_name") or ship.get("first_name") or bill.get("first_name")
        last_name  = customer.get("last_name")  or ship.get("last_name")  or bill.get("last_name")
        name = (f"{first_name or ''} {last_name or ''}".strip()) or None

        # Customer ID may be absent on guest checkouts ("" or 0) — coerce to None
        # so downstream code can distinguish "missing" from a real ID.
        cust_id_raw = customer.get("id")
        cust_id = str(cust_id_raw) if cust_id_raw else None

        # Phone fallback chain — Shopify guest checkouts often leave the customer
        # object empty but populate phone on the order or in shipping/billing.
        # Each match raises Meta EMQ ~5-15 points.
        phone = (
            customer.get("phone")
            or data.get("phone")
            or ship.get("phone")
            or bill.get("phone")
        )

        # Per-field fallback across customer.default_address → shipping → billing.
        # Previously took the first non-empty address dict whole, losing zip when
        # the chosen address didn't carry it (the other addresses often do).
        return CustomerData(
            id=cust_id,
            email=customer.get("email") or data.get("email") or data.get("contact_email"),
            name=name,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            address=self._merge_address_fields(
                customer.get("default_address") or {},
                ship,
                bill,
            ),
        )

    def _parse_items(self, line_items: list) -> List[OrderItem]:
        items = []
        for item in line_items or []:
            price = float(item.get("price", 0))
            qty = int(item.get("quantity", 1))
            items.append(
                OrderItem(
                    id=str(item.get("id", "")),
                    product_id=str(item.get("product_id", "")),
                    variant_id=str(item.get("variant_id", "")),
                    name=item.get("name") or item.get("title"),
                    sku=item.get("sku"),
                    price=price,
                    quantity=qty,
                    total=price * qty,
                )
            )
        return items

    def _parse_order(self, data: dict) -> Optional[OrderData]:
        if not data.get("id"):
            return None
        shipping_set = data.get("total_shipping_price_set") or {}
        shipping = float(
            (shipping_set.get("shop_money") or {}).get("amount", 0)
        )
        return OrderData(
            id=str(data.get("id")),
            number=str(data.get("order_number", "")),
            status=data.get("financial_status") or data.get("fulfillment_status"),
            total=float(data.get("total_price", 0)),
            subtotal=float(data.get("subtotal_price", 0)),
            tax=float(data.get("total_tax", 0)),
            shipping=shipping,
            discount=float(data.get("total_discounts", 0)),
            currency=data.get("currency"),
            items=self._parse_items(data.get("line_items", [])),
            payment_method=data.get("payment_gateway"),
        )

    # ------------------------------------------------------------------ #
    # Normalize                                                            #
    # ------------------------------------------------------------------ #

    def _parse_refund(self, data: dict) -> Optional[OrderData]:
        """
        Refund webhooks have a different structure than orders. The `id` is
        the refund_id, `order_id` references the original order, and the
        refund total comes from summing the refund transactions.
        """
        order_id = data.get("order_id")
        if not order_id:
            return None
        transactions = data.get("transactions") or []
        refund_total = 0.0
        currency = "BRL"
        for tx in transactions:
            if tx.get("kind") == "refund" and tx.get("status") == "success":
                try:
                    refund_total += float(tx.get("amount", 0))
                except (ValueError, TypeError):
                    pass
                currency = tx.get("currency") or currency
        return OrderData(
            id=str(order_id),
            number=str(order_id),
            status="refunded",
            total=refund_total,
            currency=currency,
        )

    def _parse_note_attributes(self, payload: dict) -> dict:
        """
        Extract our injected cart attributes (visitor cookie, fbp, fbc, etc).

        These are pushed by the JS pixel via POST /cart/update.js with names
        prefixed `_` so Shopify keeps them hidden from the merchant UI but
        forwards them to the order webhook as note_attributes:
            [{"name": "_etv", "value": "..."}, {"name": "_fbp", "value": "..."}]

        This is the bridge that lets us match an order back to the browse
        session even when the customer is sent to an external gateway (PIX)
        and never reaches the Shopify thank-you page.
        """
        attrs = payload.get("note_attributes") or []
        if not isinstance(attrs, list):
            return {}
        out: dict = {}
        for entry in attrs:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            value = entry.get("value")
            if not name or value in (None, ""):
                continue
            out[name] = value
        return out

    def normalize(
        self, payload: dict, client_id: str, headers: Optional[dict] = None
    ) -> NormalizedEvent:
        headers = headers or {}
        topic = headers.get("x-shopify-topic") or headers.get("X-Shopify-Topic", "")
        event_type = self.TOPIC_MAP.get(topic, EventType.CUSTOM)

        created_at = payload.get("created_at") or payload.get("updated_at") or payload.get("processed_at")
        try:
            timestamp = datetime.fromisoformat(
                str(created_at).replace("Z", "+00:00")
            )
        except Exception:
            timestamp = datetime.utcnow()

        landing_site = payload.get("landing_site") or payload.get("landing_site_ref", "")
        qs_raw = parse_qs(urlparse(landing_site).query) if landing_site else {}
        qs = {k: v[0] for k, v in qs_raw.items() if v}

        # Refunds have a different shape — use the refund parser
        if event_type == EventType.ORDER_REFUNDED:
            order = self._parse_refund(payload)
        else:
            order = self._parse_order(payload)

        # note_attributes carry our injected browse-session identifiers.
        # Prefer these over landing_site qs since they're tied to the actual
        # cart, not just the first URL the customer hit.
        nattr = self._parse_note_attributes(payload)

        # Shipping address — denormalized for country/state/city dashboard filters
        shipping = payload.get("shipping_address") or payload.get("billing_address") or {}

        client_details = payload.get("client_details") or {}
        browser_ip = payload.get("browser_ip") or client_details.get("browser_ip")
        user_agent = client_details.get("user_agent")
        source_name = payload.get("source_name")
        referring_site = payload.get("referring_site")

        return NormalizedEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            platform=self.platform_name,
            client_id=client_id,
            timestamp=timestamp,
            customer=self._parse_customer(payload),
            order=order,
            utm=self._infer_utm(landing_site, referring_site, source_name, nattr),
            raw_payload=payload,
            metadata={
                "shop_domain":       headers.get("x-shopify-shop-domain"),
                "topic":             topic,
                "source_name":       payload.get("source_name"),
                "landing_site":      landing_site,
                "gclid":             nattr.get("_gclid")  or qs.get("gclid"),
                "gbraid":            nattr.get("_gbraid") or qs.get("gbraid"),
                "wbraid":            nattr.get("_wbraid") or qs.get("wbraid"),
                "fbclid":            qs.get("fbclid"),
                "referring_site":    payload.get("referring_site"),
                "cart_token":        payload.get("cart_token"),
                "refund_id":         str(payload.get("id")) if event_type == EventType.ORDER_REFUNDED else None,
                # Browse-session attribution from note_attributes
                "visitor_cookie_id": nattr.get("_etv"),
                "fbp":               nattr.get("_fbp"),
                "fbc":               nattr.get("_fbc"),
                "ga_client_id":      nattr.get("_gcid"),
                "ttclid":            nattr.get("_ettc"),
                "ttp":               nattr.get("_ttp"),
                # Facebook Login ID & Date of Birth — improves Meta CAPI EMQ (+8% and +6%)
                "facebook_login":    nattr.get("_fblogin"),
                "date_of_birth":     nattr.get("_dob"),
                # Shipping address — used to populate orders.shipping_country/state/city
                "shipping_country":  shipping.get("country_code") or shipping.get("country"),
                "shipping_state":    shipping.get("province_code") or shipping.get("province"),
                "shipping_city":     shipping.get("city"),
                # Browser identifiers for Meta CAPI EMQ — Shopify includes these on every order
                "ip":                browser_ip,
                "user_agent":        user_agent,
                # Order confirmation page URL — used as event_source_url in CAPI
                "order_status_url":  payload.get("order_status_url"),
            },
        )
