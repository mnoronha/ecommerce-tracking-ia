"""
Agency API v1.1 — canonical enums.

These enums are the source of truth for both the API contracts and the
future SQL migrations (core_data_sources, core_metric_snapshots, etc.).
Every enum member maps directly to a PRD Section 5 entry.
"""

from enum import Enum


class SourceState(str, Enum):
    """State of a data source (core_data_sources, core_data_health)."""
    READY            = "READY"
    PARTIAL          = "PARTIAL"
    STALE            = "STALE"
    NO_DATA          = "NO_DATA"
    MISSING          = "MISSING"
    ACCESS_MISSING   = "ACCESS_MISSING"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NOT_CONTRACTED   = "NOT_CONTRACTED"
    NOT_APPLICABLE   = "NOT_APPLICABLE"
    ERROR            = "ERROR"


class ValueStatus(str, Enum):
    """State of a specific metric value (core_metric_snapshots, metric contracts)."""
    OK             = "OK"
    PARTIAL        = "PARTIAL"
    STALE          = "STALE"
    NO_DATA        = "NO_DATA"
    MISSING        = "MISSING"
    UNKNOWN        = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ContractStatus(str, Enum):
    CONTRACTED     = "CONTRACTED"
    NOT_CONTRACTED = "NOT_CONTRACTED"
    PENDING        = "PENDING"


class CertificationStatus(str, Enum):
    PROVISIONAL = "PROVISIONAL"
    CERTIFIED   = "CERTIFIED"
    SUPERSEDED  = "SUPERSEDED"


class BusinessModel(str, Enum):
    ECOMMERCE             = "ecommerce"
    LEAD_GENERATION       = "lead_generation"
    LOCAL_LEAD_GENERATION = "local_lead_generation"
    AUCTION               = "auction"


class VisibilityScope(str, Enum):
    AGENCY_ONLY    = "AGENCY_ONLY"
    CLIENT_VISIBLE = "CLIENT_VISIBLE"


class ChangeLogSource(str, Enum):
    AUTO_GOOGLE_ADS = "AUTO_GOOGLE_ADS"
    AUTO_META       = "AUTO_META"
    HUMAN           = "HUMAN"


class MatchStatus(str, Enum):
    MATCHED               = "MATCHED"
    AUTO_PENDING_CONTEXT  = "AUTO_PENDING_CONTEXT"
    HUMAN_UNCONFIRMED     = "HUMAN_UNCONFIRMED"


class NarrativeStatus(str, Enum):
    DRAFT             = "DRAFT"
    READY_FOR_REVIEW  = "READY_FOR_REVIEW"
    APPROVED          = "APPROVED"
    PUBLISHED         = "PUBLISHED"
    SUPERSEDED        = "SUPERSEDED"


class Level(str, Enum):
    """Generic LOW / MEDIUM / HIGH — used for confidence, priority and risk."""
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


class EvidenceLevel(str, Enum):
    OBSERVATION = "OBSERVATION"
    WEAK        = "WEAK"
    MODERATE    = "MODERATE"
    STRONG      = "STRONG"


class JobStatus(str, Enum):
    QUEUED    = "QUEUED"
    RUNNING   = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED    = "FAILED"
    DEAD      = "DEAD"


class ActionEventType(str, Enum):
    APPROVED             = "APPROVED"
    IGNORED              = "IGNORED"
    EXECUTED_CONFIRMED   = "EXECUTED_CONFIRMED"
    RESOLVED             = "RESOLVED"
    EXPIRED              = "EXPIRED"


class ChangeType(str, Enum):
    BUDGET          = "BUDGET"
    TARGET_ROAS     = "TARGET_ROAS"
    TARGET_CPA      = "TARGET_CPA"
    BID_STRATEGY    = "BID_STRATEGY"
    CREATIVE_LAUNCH = "CREATIVE_LAUNCH"
    CREATIVE_PAUSE  = "CREATIVE_PAUSE"
    CAMPAIGN_LAUNCH = "CAMPAIGN_LAUNCH"
    CAMPAIGN_PAUSE  = "CAMPAIGN_PAUSE"
    AUDIENCE        = "AUDIENCE"
    LANDING_PAGE    = "LANDING_PAGE"
    OFFER           = "OFFER"
    TRACKING        = "TRACKING"
    OTHER           = "OTHER"


class ChangeConfidence(str, Enum):
    """Confidence for change log entries (distinct from Level used in intel objects)."""
    CONFIRMED = "CONFIRMED"
    PROBABLE  = "PROBABLE"
    UNKNOWN   = "UNKNOWN"


class ReportType(str, Enum):
    WEEKLY  = "WEEKLY"
    MONTHLY = "MONTHLY"


class TargetStatus(str, Enum):
    OK       = "OK"
    UNKNOWN  = "UNKNOWN"
    BEHIND   = "BEHIND"
    AT_RISK  = "AT_RISK"
    MET      = "MET"


class AlertStatus(str, Enum):
    OPEN         = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED     = "RESOLVED"


class RecommendationStatus(str, Enum):
    PENDING_REVIEW       = "PENDING_REVIEW"
    APPROVED             = "APPROVED"
    IGNORED              = "IGNORED"
    EXECUTED_CONFIRMED   = "EXECUTED_CONFIRMED"
    RESOLVED             = "RESOLVED"
    EXPIRED              = "EXPIRED"


class ServiceStatus(str, Enum):
    UP       = "UP"
    DOWN     = "DOWN"
    DEGRADED = "DEGRADED"


class AlertRuleSuggestionStatus(str, Enum):
    PENDING  = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class LearningScope(str, Enum):
    CLIENT         = "CLIENT"
    BUSINESS_MODEL = "BUSINESS_MODEL"
    AGENCY         = "AGENCY"


class LearningCandidateStatus(str, Enum):
    PENDING  = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class MetricDomain(str, Enum):
    """
    Semantic domain of a MetricValue — what truth the metric belongs to.
    Distinct from the source system that provided the raw data.

    Source systems (google_ads, meta_ads, ga4, shopify) are NOT MetricDomain.
    The Core is the sole authority for assigning domain.
    Hermes treats a missing or unknown domain as None; it never infers or assigns domain.

    Separation from DataHealthEntry.domain (which mixes semantic + source) is intentional:
    MetricDomain is the semantic layer only.
    """
    BUSINESS   = "BUSINESS"   # Revenue, GMV, orders, profitability, LTV
    ADS        = "ADS"        # Paid channel: spend, ROAS, CPA, CTR, frequency
    JOURNEY    = "JOURNEY"    # Web journey: sessions, funnel, bounce rate, engagement
    CONVERSION = "CONVERSION" # Tracked conversions, attribution quality, match rate
