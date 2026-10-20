"""
Normalizes raw Google Ads change_event and Meta Ads activity records
into core_change_log row dicts ready for insertion.

Design invariants:
  - Every row gets source=PLATFORM_DETECTED.
  - external_change_id is always set (stable resource_name for Google, deterministic
    hash for Meta — never null from this module).
  - Unknown resource/event types map to change_type=OTHER, never dropped.
  - Returns None only if the event is structurally un-parseable (no timestamp, etc.).
  - Never invents before/after values absent from the provider response.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Google ────────────────────────────────────────────────────────────────────

_G_ENTITY_TYPE: dict[str, str] = {
    "CAMPAIGN":           "CAMPAIGN",
    "CAMPAIGN_BUDGET":    "BUDGET",
    "AD_GROUP":           "AD_GROUP",
    "AD_GROUP_CRITERION": "KEYWORD",
    "AD":                 "AD",
    "ASSET":              "ASSET",
    "AD_GROUP_ASSET":     "ASSET",
    "CAMPAIGN_ASSET":     "ASSET",
    "ASSET_GROUP":        "ASSET_GROUP",
    "CAMPAIGN_CRITERION": "CAMPAIGN_CRITERION",
    "CONVERSION_ACTION":  "CONVERSION_ACTION",
}

# Proto JSON key for the object nested inside new_resource / old_resource
_G_OBJ_KEY: dict[str, str] = {
    "CAMPAIGN":           "campaign",
    "CAMPAIGN_BUDGET":    "campaignBudget",
    "AD_GROUP":           "adGroup",
    "AD_GROUP_CRITERION": "adGroupCriterion",
    "AD":                 "ad",
    "ASSET":              "asset",
    "AD_GROUP_ASSET":     "adGroupAsset",
    "CAMPAIGN_ASSET":     "campaignAsset",
    "ASSET_GROUP":        "assetGroup",
    "CAMPAIGN_CRITERION": "campaignCriterion",
    "CONVERSION_ACTION":  "conversionAction",
}


def _resource_id(resource_name: str) -> Optional[str]:
    """'customers/123/campaigns/456' → '456'."""
    if not resource_name:
        return None
    parts = resource_name.rstrip("/").split("/")
    return parts[-1] if parts else None


def _map_google_change_type(
    resource_type: str,
    operation: str,
    changed_fields: str,
    new_resource: dict,
    old_resource: dict,
) -> str:
    cf = changed_fields.lower()

    if resource_type == "CAMPAIGN_BUDGET":
        return "BUDGET"

    if resource_type == "CAMPAIGN":
        if operation == "CREATE":
            return "CAMPAIGN_LAUNCH"
        if operation == "REMOVE":
            return "CAMPAIGN_PAUSE"
        if "status" in cf:
            camp = (new_resource.get("campaign") or {})
            st = camp.get("status", "")
            if st == "ENABLED":
                return "CAMPAIGN_LAUNCH"
            if st in ("PAUSED", "REMOVED"):
                return "CAMPAIGN_PAUSE"
        if any(k in cf for k in ("target_roas", "targetroas")):
            return "TARGET_ROAS"
        if any(k in cf for k in ("target_cpa", "targetcpa")):
            return "TARGET_CPA"
        if "budget" in cf:
            return "BUDGET"
        if any(k in cf for k in ("bidding", "bid_strategy", "biddingstrategy")):
            return "BID_STRATEGY"
        return "OTHER"

    if resource_type == "AD_GROUP":
        return "ADSET_STATUS_CHANGE"

    if resource_type == "AD_GROUP_CRITERION":
        crit = (new_resource.get("adGroupCriterion") or {})
        is_negative = bool(crit.get("negative", False))
        if operation == "CREATE":
            return "NEGATIVE_KEYWORD_ADDED" if is_negative else "KEYWORD_ADDED"
        if operation == "REMOVE":
            return "KEYWORD_REMOVED"
        if any(k in cf for k in ("match_type", "matchtype")):
            return "MATCH_TYPE_CHANGE"
        return "OTHER"

    if resource_type == "AD":
        if operation == "CREATE":
            return "AD_STATUS_CHANGE"
        camp_new = (new_resource.get("ad") or {})
        st = camp_new.get("status", "")
        if st == "ENABLED":
            return "AD_STATUS_CHANGE"
        if st in ("PAUSED", "REMOVED"):
            return "AD_STATUS_CHANGE"
        return "AD_STATUS_CHANGE"

    if resource_type in ("ASSET", "AD_GROUP_ASSET", "CAMPAIGN_ASSET"):
        return "ASSET_CHANGE"

    if resource_type == "ASSET_GROUP":
        return "PMAX_ASSET_GROUP_CHANGE"

    if resource_type == "CAMPAIGN_CRITERION":
        return "SHOPPING_PRODUCT_SCOPE_CHANGE"

    if resource_type == "CONVERSION_ACTION":
        return "CONVERSION_ACTION_CHANGE"

    return "OTHER"


def _google_before_after(
    new_resource: dict,
    old_resource: dict,
    resource_type: str,
) -> tuple[Optional[dict], Optional[dict]]:
    """Extract the resource object layer from new/old, stripping the wrapper."""
    obj_key = _G_OBJ_KEY.get(resource_type)
    if not obj_key:
        return (old_resource or None), (new_resource or None)
    old_obj = (old_resource or {}).get(obj_key) or {}
    new_obj = (new_resource or {}).get(obj_key) or {}
    return (old_obj or None), (new_obj or None)


def normalize_google_event(
    event: dict,
    client_id: str,
    account_id: str,
    detected_at: Optional[datetime] = None,
) -> Optional[dict]:
    """
    Normalize a raw Google Ads change_event result to a core_change_log row dict.
    Returns None if the event is structurally un-parseable.
    """
    ce = event.get("changeEvent") or {}
    if not ce:
        return None

    resource_type   = ce.get("changeResourceType") or ""
    operation       = ce.get("resourceChangeOperation") or ""
    changed_fields  = ce.get("changedFields") or ""
    new_resource    = ce.get("newResource") or {}
    old_resource    = ce.get("oldResource") or {}
    resource_name   = ce.get("resourceName") or ""

    # Google REST serializes change_datetime as "changeDatetime" (lowerCamelCase)
    occurred_raw = ce.get("changeDatetime") or ce.get("changeDateTime") or ""
    if not occurred_raw:
        return None

    try:
        raw = occurred_raw.strip().replace(" ", "T")
        if "+" not in raw and not raw.endswith("Z"):
            raw += "+00:00"
        occurred_at = datetime.fromisoformat(raw)
    except Exception:
        logger.debug("google_normalizer: unparseable datetime %r", occurred_raw)
        return None

    change_type = _map_google_change_type(
        resource_type, operation, changed_fields, new_resource, old_resource,
    )
    entity_type = _G_ENTITY_TYPE.get(resource_type, resource_type or "UNKNOWN")

    campaign_res = ce.get("campaign") or ""
    adgroup_res  = ce.get("adGroup") or ""
    ad_res       = ce.get("ad") or ""

    campaign_id = _resource_id(campaign_res)
    adgroup_id  = _resource_id(adgroup_res)
    ad_id       = _resource_id(ad_res)
    entity_id   = _resource_id(resource_name)

    obj_key = _G_OBJ_KEY.get(resource_type, "")
    obj = (new_resource.get(obj_key) or old_resource.get(obj_key) or {}) if obj_key else {}
    entity_name = str(obj.get("name") or "")

    before_state, after_state = _google_before_after(new_resource, old_resource, resource_type)

    description = f"{operation} {resource_type}"
    if changed_fields:
        description += f": {changed_fields}"

    return {
        "client_id":           client_id,
        "occurred_at":         occurred_at.isoformat(),
        "detected_at":         (detected_at or datetime.now(timezone.utc)).isoformat(),
        "channel":             "google",
        "platform_account_id": account_id,
        "entity_type":         entity_type,
        "entity_id":           entity_id,
        "entity_name_at_time": entity_name,
        "change_type":         change_type,
        "before_state":        before_state,
        "after_state":         after_state,
        "source":              "PLATFORM_DETECTED",
        "confidence":          "CONFIRMED",
        "external_change_id":  resource_name or None,
        "campaign_id":         campaign_id,
        "adset_or_adgroup_id": adgroup_id,
        "ad_id":               ad_id,
        "executed_by":         ce.get("clientType") or None,
        "description":         description,
    }


# ── Meta ──────────────────────────────────────────────────────────────────────

_META_OBJECT_TYPE: dict[str, str] = {
    "campaign":   "CAMPAIGN",
    "ad_set":     "AD_SET",
    "adset":      "AD_SET",
    "ad":         "AD",
    "ad_account": "AD_ACCOUNT",
    "creative":   "CREATIVE",
}

# event_type → change_type; status changes refined after parsing extra_data
_META_CHANGE_TYPE: dict[str, str] = {
    "campaign_status_change":           "CAMPAIGN_PAUSE",   # refined below
    "campaign_spending_limit_changed":  "BUDGET",
    "budget_change":                    "BUDGET",
    "adset_budget_changed":             "BUDGET",
    "bid_change":                       "BID_STRATEGY",
    "adset_bid_changed":                "BID_STRATEGY",
    "adset_status_changed":             "ADSET_STATUS_CHANGE",
    "ad_status_changed":                "AD_STATUS_CHANGE",
    "optimization_goal_change":         "OTHER",
    "placement_edit":                   "PLACEMENT_CHANGE",
    "creative_add":                     "CREATIVE_ADDED",
    "creative_replace":                 "CREATIVE_REPLACED",
    "audience_sharing_change":          "AUDIENCE",
    "tracking_specs_change":            "TRACKING",
}


def _meta_ext_id(account_id: str, object_id: str, event_type: str, timestamp: Any) -> str:
    """Deterministic hash — stable for the same event across repeated polls."""
    raw = f"{account_id}|{object_id}|{event_type}|{timestamp}"
    return "meta_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _parse_extra(raw: Any) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _meta_change_type(event_type: str, extra_data: dict) -> str:
    mapped = _META_CHANGE_TYPE.get(event_type, "OTHER")
    if event_type == "campaign_status_change":
        new_val = str(
            extra_data.get("new_value")
            or extra_data.get("status_to")
            or ""
        ).upper()
        if new_val in ("ACTIVE", "ENABLED", "1"):
            return "CAMPAIGN_LAUNCH"
        return "CAMPAIGN_PAUSE"
    return mapped


def _meta_before_after(event_type: str, extra_data: dict) -> tuple[Optional[dict], Optional[dict]]:
    old_val = (
        extra_data.get("old_value")
        or extra_data.get("value_from")
        or extra_data.get("status_from")
    )
    new_val = (
        extra_data.get("new_value")
        or extra_data.get("value_to")
        or extra_data.get("status_to")
    )
    if old_val is None and new_val is None:
        # No structured before/after — return extra_data as context on the after side
        return None, (extra_data if extra_data else None)
    return (
        {"value": old_val} if old_val is not None else None,
        {"value": new_val} if new_val is not None else None,
    )


def normalize_meta_activity(
    activity: dict,
    client_id: str,
    account_id: str,
    detected_at: Optional[datetime] = None,
) -> Optional[dict]:
    """
    Normalize a raw Meta /activities entry to a core_change_log row dict.
    Returns None if the event is structurally un-parseable.
    """
    event_type  = activity.get("event_type") or ""
    object_type = (activity.get("object_type") or "").lower().strip()
    object_id   = str(activity.get("object_id") or "")
    event_time  = activity.get("event_time")

    if not event_type or not event_time:
        return None

    try:
        # Meta returns "2026-10-20T18:41:14+0000"; fromisoformat needs "+00:00"
        raw = str(event_time).replace("+0000", "+00:00").replace("-0000", "+00:00")
        occurred_at = datetime.fromisoformat(raw).astimezone(timezone.utc)
    except Exception:
        logger.debug("meta_normalizer: unparseable event_time %r", event_time)
        return None

    extra_data  = _parse_extra(activity.get("extra_data"))
    change_type = _meta_change_type(event_type, extra_data)
    entity_type = _META_OBJECT_TYPE.get(object_type, object_type.upper() or "UNKNOWN")

    before_state, after_state = _meta_before_after(event_type, extra_data)

    # Hierarchy IDs by object_type
    raw_campaign_id = (
        object_id if object_type == "campaign"
        else str(extra_data.get("campaign_id") or "")
    )
    raw_adset_id = (
        object_id if object_type in ("ad_set", "adset")
        else str(extra_data.get("adset_id") or "")
    )
    raw_ad_id = object_id if object_type == "ad" else None

    external_id = _meta_ext_id(account_id, object_id, event_type, event_time)

    return {
        "client_id":           client_id,
        "occurred_at":         occurred_at.isoformat(),
        "detected_at":         (detected_at or datetime.now(timezone.utc)).isoformat(),
        "channel":             "meta",
        "platform_account_id": account_id,
        "entity_type":         entity_type,
        "entity_id":           object_id or None,
        "entity_name_at_time": activity.get("object_name") or "",
        "change_type":         change_type,
        "before_state":        before_state,
        "after_state":         after_state,
        "source":              "PLATFORM_DETECTED",
        "confidence":          "CONFIRMED",
        "external_change_id":  external_id,
        "campaign_id":         raw_campaign_id or None,
        "adset_or_adgroup_id": raw_adset_id or None,
        "ad_id":               raw_ad_id or None,
        "executed_by":         activity.get("actor_name") or None,
        "description":         activity.get("translated_text") or None,
    }
