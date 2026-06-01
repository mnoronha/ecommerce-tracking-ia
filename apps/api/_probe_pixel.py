"""Temp: probe whether our token still has access to the LK Meta pixel + ad account.
Prints only Graph API responses (never the token)."""
import httpx
from app.database import get_supabase

CLIENT_ID = "3e20e8b9-c1b5-449f-bc5c-eb0a00704387"
GV = "https://graph.facebook.com/v19.0"

c = (get_supabase().table("clients")
     .select("meta_pixel_id, meta_ad_account_id, meta_access_token")
     .eq("id", CLIENT_ID).limit(1).execute().data[0])
pixel = c["meta_pixel_id"]; acct = c["meta_ad_account_id"]; tok = c["meta_access_token"]


def show(label, r):
    print(f"\n=== {label} -> HTTP {r.status_code} ===")
    try:
        print(r.json())
    except Exception:
        print(r.text[:300])


# 1) Pixel object: name + last_fired_time (when Meta last received ANY event)
r = httpx.get(f"{GV}/{pixel}",
              params={"access_token": tok,
                      "fields": "name,last_fired_time,is_unavailable,code"},
              timeout=30)
show(f"PIXEL {pixel}", r)

# 2) Who the token belongs to
r2 = httpx.get(f"{GV}/me", params={"access_token": tok, "fields": "id,name"}, timeout=30)
show("TOKEN OWNER (/me)", r2)

# 3) Ad account access + status
acct_id = acct if str(acct).startswith("act_") else f"act_{acct}"
r3 = httpx.get(f"{GV}/{acct_id}",
               params={"access_token": tok,
                       "fields": "name,account_status,disable_reason,amount_spent,currency"},
               timeout=30)
show(f"AD ACCOUNT {acct_id}", r3)

# 4) Pixel stats — recent server/browser activity counts (last 24h buckets)
r4 = httpx.get(f"{GV}/{pixel}/stats",
               params={"access_token": tok, "aggregation": "event", "limit": 50},
               timeout=30)
show("PIXEL /stats (event aggregation)", r4)
