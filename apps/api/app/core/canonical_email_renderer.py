"""
NoroLabs Canonical Email Renderer — v4.0-dark
Design system: dark navy, NoroLabs turquoise, executive only.
No technical metadata. Client-facing language only.
"""
from __future__ import annotations
from typing import Optional
import logging

log = logging.getLogger(__name__)

TEMPLATE_VERSION = "v4.0-dark"
PLATFORM_BASE_URL = "https://track.noroia.com.br"
_KPI_TOLERANCE = 0.03   # 3 % relative tolerance for validation

# ── Design tokens ────────────────────────────────────────────────────────────
_C = {
    "bg":         "#080E1A",
    "card":       "#0E1725",
    "card_hi":    "#132030",
    "border":     "#1C2B3F",
    "sep":        "#1A2840",
    "accent":     "#00C4B4",
    "positive":   "#00C4B4",
    "negative":   "#F06565",
    "neutral":    "#4E6580",
    "text_1":     "#EEF2F8",
    "text_2":     "#8BA0B8",
    "text_3":     "#4E6580",
    "white":      "#FFFFFF",
    "hdr":        "#050B14",
    "cta_bg":     "#00C4B4",
    "cta_text":   "#05101C",
    "meta_blue":  "#1877F2",
    "goog_green": "#34A853",
}


# ── Formatters ───────────────────────────────────────────────────────────────

def _brl(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _num(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{int(round(v)):,}".replace(",", ".")


def _num_conv(v: Optional[float]) -> str:
    """Show canonical value: integer when whole, 2 decimals when fractional."""
    if v is None:
        return "—"
    if abs(v - round(v)) < 0.001:   # essentially an integer
        return _num(v)
    # Use 2 decimals to preserve canonical value (e.g. 12.96 not 13.0)
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(v: Optional[float]) -> str:
    if v is None:
        return ""
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.1f}%".replace(".", ",")


def _x(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:.2f}x".replace(".", ",")


def _delta_color(v: Optional[float], invert: bool = False) -> str:
    if v is None or abs(v) < 1.0:
        return _C["neutral"]
    good = v > 0 if not invert else v < 0
    return _C["positive"] if good else _C["negative"]


# ── Client-facing text cleaner ────────────────────────────────────────────────

_TEXT_REPLACEMENTS = [
    # Internal jargon → client language
    ("SMALL_SAMPLE", "baixo volume da amostra"),
    ("small_sample", "baixo volume da amostra"),
    ("canônica do canal", "do canal"),
    ("deterioração canônica", "queda"),
    ("canônico", ""),
    ("canônica", ""),
    ("Business Truth", "dados de negócio"),
    ("business truth", "dados de negócio"),
    ("dados PROVISIONAL", "dados disponíveis"),
    ("PROVISIONAL", ""),
    ("UNKNOWN", "não verificado"),
    # Specific verbose phrases
    (
        "O CPA blended de R$ 41,51 é dominado por conversões que não devem ser tratadas como vendas.",
        "Conversões secundárias continuam distorcendo o CPA blended. "
        "Para decisões de mídia, usar somente compras, receita, ROAS e CPA de ecommerce.",
    ),
    (
        "Usar somente KPIs canônicos de ecommerce para decisões e tratar a granularidade "
        "diagnóstica como SMALL_SAMPLE.",
        "Usar somente dados de ecommerce (compras, receita, ROAS e CPA) para decisões de mídia, "
        "considerando o baixo volume da amostra.",
    ),
    (
        "tratar a granularidade diagnóstica como SMALL_SAMPLE",
        "considerar o baixo volume da amostra antes de tomar decisões",
    ),
    (
        "tratar a granularidade diagnóstica com SMALL_SAMPLE",
        "considerar o baixo volume da amostra antes de tomar decisões",
    ),
    # Remove double spaces left by replacements
    ("  ", " "),
]


def _clean(text: str) -> str:
    if not text:
        return text
    for old, new in _TEXT_REPLACEMENTS:
        text = text.replace(old, new)
    return text.strip()


# ── KPI validation ────────────────────────────────────────────────────────────

def _validate_kpis(facts: dict) -> tuple[bool, list[str]]:
    """Assert derived KPIs against raw values. Returns (ok, [errors])."""
    errors: list[str] = []

    def _chk(label: str, stored: Optional[float], computed: Optional[float]) -> None:
        if not stored or not computed:
            return
        delta = abs(stored - computed) / max(abs(stored), abs(computed))
        if delta > _KPI_TOLERANCE:
            errors.append(
                f"{label}: stored={stored:.4f} computed={computed:.4f} delta={delta:.1%}"
            )

    rev     = facts.get("revenue_business")
    orders  = facts.get("orders_count")
    spend   = facts.get("total_spend")
    ms      = facts.get("meta_spend")
    mc      = facts.get("meta_conversions")
    mv      = facts.get("meta_conversion_value")
    gs      = facts.get("google_spend")
    gp      = facts.get("google_purchase_conversions")
    gv      = facts.get("google_purchase_conversion_value")

    if rev and spend:
        _chk("MER", facts.get("mer"), rev / spend)
    if mv and ms:
        _chk("Meta ROAS", facts.get("roas_meta"), mv / ms)
    if ms and mc:
        _chk("Meta CPA", facts.get("cpa_meta"), ms / mc)
    if gv and gs:
        _chk("Google ROAS ecom", facts.get("google_roas_ecommerce"), gv / gs)
    if gs and gp:
        _chk("Google CPA ecom", facts.get("google_cpa_ecommerce"), gs / gp)

    return (len(errors) == 0, errors)


# ── Period formatters ─────────────────────────────────────────────────────────

_PT_MONTHS = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
              "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
_PT_MONTHS_FULL = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
                   "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]


def _fmt_period(start: str, end: str) -> str:
    try:
        import datetime as dt
        s = dt.date.fromisoformat(start)
        e = dt.date.fromisoformat(end)
        sm = _PT_MONTHS[s.month - 1]
        em = _PT_MONTHS[e.month - 1]
        if s.month == e.month and s.year == e.year:
            return f"{s.day} a {e.day} de {sm} {s.year}"
        return f"{s.day} {sm} a {e.day} {em} {e.year}"
    except Exception:
        return f"{start} a {end}"


def _fmt_period_monthly(start: str, end: str) -> str:
    try:
        import datetime as dt
        s = dt.date.fromisoformat(start)
        mf = _PT_MONTHS_FULL[s.month - 1]
        end_day = end.split("-")[2]
        return f"{mf} {s.year} &nbsp;&middot;&nbsp; {s.day} a {end_day} de {_PT_MONTHS[s.month-1]}"
    except Exception:
        return f"{start} a {end}"


def _fmt_client(client_id: str) -> str:
    mapping = {
        "lk-sneakers": "LK Sneakers",
        "enutri":       "E-Nutri",
    }
    return mapping.get(client_id, client_id.replace("-", " ").title())


def _platform_url(client_id: str) -> str:
    return f"{PLATFORM_BASE_URL}/clients/{client_id}/canonical-reports"


# ── HTML helpers ─────────────────────────────────────────────────────────────

def _section_label(text: str) -> str:
    return (
        f'<div style="font-size:10px;font-weight:700;letter-spacing:0.12em;'
        f'text-transform:uppercase;color:{_C["text_3"]};'
        f'font-family:Arial,Helvetica,sans-serif;'
        f'padding-bottom:12px;margin-bottom:16px;'
        f'border-bottom:1px solid {_C["sep"]};">{text}</div>'
    )


# ── Section builders ──────────────────────────────────────────────────────────

def _header(client_name: str, report_label: str, period_label: str) -> str:
    return f"""<tr>
  <td bgcolor="{_C['hdr']}" style="background-color:{_C['hdr']};padding:28px 28px 24px;border-radius:10px 10px 0 0;border-bottom:1px solid {_C['border']};">
    <div style="font-size:10px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:{_C['accent']};font-family:Arial,Helvetica,sans-serif;margin-bottom:10px;">NOROLABS &#215; {client_name.upper()}</div>
    <div style="font-size:20px;font-weight:700;color:{_C['white']};font-family:Arial,Helvetica,sans-serif;letter-spacing:-0.01em;line-height:1.2;">{report_label}</div>
    <div style="font-size:13px;color:{_C['text_2']};margin-top:6px;font-family:Arial,Helvetica,sans-serif;">{period_label}</div>
  </td>
</tr>"""


def _hero(revenue: float, orders: int, revenue_wow: Optional[float]) -> str:
    wow_color = _delta_color(revenue_wow)
    wow_txt   = _pct(revenue_wow)
    wow_block = ""
    if wow_txt:
        wow_block = (
            f'<td align="right" valign="bottom" style="padding-bottom:4px;">'
            f'<table cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td bgcolor="{_C["card_hi"]}" style="background-color:{_C["card_hi"]};border-radius:8px;border:1px solid {_C["border"]};padding:10px 16px;text-align:center;">'
            f'<div style="font-size:20px;font-weight:700;color:{wow_color};font-family:Arial,Helvetica,sans-serif;line-height:1;">{wow_txt}</div>'
            f'<div style="font-size:10px;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;text-transform:uppercase;letter-spacing:0.06em;margin-top:4px;">vs semana anterior</div>'
            f'</td></tr></table></td>'
        )
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:28px 28px 24px;border-bottom:1px solid {_C['sep']};">
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td valign="bottom">
          <div style="font-size:10px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;margin-bottom:10px;">RECEITA DA SEMANA</div>
          <div style="font-size:36px;font-weight:700;color:{_C['white']};font-family:Arial,Helvetica,sans-serif;line-height:1;letter-spacing:-0.02em;">{_brl(revenue)}</div>
          <div style="font-size:13px;color:{_C['text_2']};margin-top:8px;font-family:Arial,Helvetica,sans-serif;">{_num(orders)} pedidos</div>
        </td>
        {wow_block}
      </tr>
    </table>
  </td>
</tr>"""


def _hero_monthly(revenue: float, orders: int, revenue_mom: Optional[float]) -> str:
    wow_color = _delta_color(revenue_mom)
    wow_txt   = _pct(revenue_mom)
    wow_block = ""
    if wow_txt:
        wow_block = (
            f'<td align="right" valign="bottom" style="padding-bottom:4px;">'
            f'<table cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td bgcolor="{_C["card_hi"]}" style="background-color:{_C["card_hi"]};border-radius:8px;border:1px solid {_C["border"]};padding:10px 16px;text-align:center;">'
            f'<div style="font-size:20px;font-weight:700;color:{wow_color};font-family:Arial,Helvetica,sans-serif;line-height:1;">{wow_txt}</div>'
            f'<div style="font-size:10px;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;text-transform:uppercase;letter-spacing:0.06em;margin-top:4px;">vs mês anterior</div>'
            f'</td></tr></table></td>'
        )
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:28px 28px 24px;border-bottom:1px solid {_C['sep']};">
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td valign="bottom">
          <div style="font-size:10px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;margin-bottom:10px;">RECEITA DO M&#202;S</div>
          <div style="font-size:36px;font-weight:700;color:{_C['white']};font-family:Arial,Helvetica,sans-serif;line-height:1;letter-spacing:-0.02em;">{_brl(revenue)}</div>
          <div style="font-size:13px;color:{_C['text_2']};margin-top:8px;font-family:Arial,Helvetica,sans-serif;">{_num(orders)} pedidos</div>
        </td>
        {wow_block}
      </tr>
    </table>
  </td>
</tr>"""


def _kpis_row(orders: int, orders_wow: Optional[float],
              spend: float, spend_wow: Optional[float],
              mer: float, mer_wow: Optional[float],
              avg_ticket: float,
              section_label: str = "KPIs DA SEMANA") -> str:

    def card(label: str, value: str, delta: str, dcol: str, note: str = "") -> str:
        note_html = (
            f'<div style="font-size:11px;color:{_C["text_3"]};margin-top:5px;font-family:Arial,Helvetica,sans-serif;">{note}</div>'
            if note else ""
        )
        delta_html = (
            f'<div style="font-size:12px;font-weight:700;color:{dcol};margin-top:5px;font-family:Arial,Helvetica,sans-serif;">{delta}</div>'
            if delta else '<div style="height:5px;"></div>'
        )
        return (
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'<tr><td bgcolor="{_C["card_hi"]}" style="background-color:{_C["card_hi"]};border-radius:8px;border:1px solid {_C["border"]};padding:16px 16px 18px;">'
            f'<div style="font-size:10px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;margin-bottom:8px;">{label}</div>'
            f'<div style="font-size:22px;font-weight:700;color:{_C["text_1"]};font-family:Arial,Helvetica,sans-serif;line-height:1.1;">{value}</div>'
            f'{delta_html}'
            f'{note_html}'
            f'</td></tr></table>'
        )

    c1 = card("PEDIDOS",       _num(orders),    _pct(orders_wow), _delta_color(orders_wow))
    c2 = card("INVESTIMENTO",  _brl(spend),     _pct(spend_wow),  _C["neutral"])  # spend delta always neutral
    c3 = card("MER",           _x(mer),         _pct(mer_wow),    _delta_color(mer_wow),
              "Receita &divide; investimento em m&#237;dia")
    c4 = card("TICKET M&#201;DIO", _brl(avg_ticket), "", _C["neutral"])

    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label(section_label)}
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td width="50%" valign="top" style="padding-right:8px;">{c1}</td>
        <td width="50%" valign="top" style="padding-left:8px;">{c2}</td>
      </tr>
      <tr><td colspan="2" height="10" style="font-size:0;line-height:0;">&nbsp;</td></tr>
      <tr>
        <td width="50%" valign="top" style="padding-right:8px;">{c3}</td>
        <td width="50%" valign="top" style="padding-left:8px;">{c4}</td>
      </tr>
    </table>
  </td>
</tr>"""


def _kpis_row_monthly(orders: int, orders_mom: Optional[float],
                      spend: float, spend_mom: Optional[float],
                      mer: float, mer_mom: Optional[float],
                      avg_ticket: float) -> str:
    return _kpis_row(orders, orders_mom, spend, spend_mom, mer, mer_mom, avg_ticket,
                     section_label="KPIs DO M&#202;S")


def _channel_card(name: str, name_color: str,
                  rows: list[tuple[str, str, Optional[float], bool]]) -> str:
    rows_html = ""
    for label, value, delta_v, inv in rows:
        if delta_v is not None:
            # spend rows: always neutral
            dc = _C["neutral"] if "nvestimento" in label or "pend" in label else _delta_color(delta_v, invert=inv)
            dt = _pct(delta_v)
            delta_span = f' <span style="font-size:11px;font-weight:700;color:{dc};">({dt})</span>'
        else:
            delta_span = ""
        rows_html += (
            f'<tr>'
            f'<td style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;padding:7px 0;border-bottom:1px solid {_C["sep"]};">{label}</td>'
            f'<td align="right" style="font-size:12px;font-weight:600;color:{_C["text_1"]};font-family:Arial,Helvetica,sans-serif;padding:7px 0;border-bottom:1px solid {_C["sep"]};">{value}{delta_span}</td>'
            f'</tr>'
        )
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td bgcolor="{_C["card_hi"]}" style="background-color:{_C["card_hi"]};border-radius:8px;border:1px solid {_C["border"]};padding:16px 16px 8px;">'
        f'<div style="font-size:12px;font-weight:700;color:{name_color};font-family:Arial,Helvetica,sans-serif;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:12px;">{name}</div>'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">{rows_html}</table>'
        f'</td></tr></table>'
    )


def _channels(meta_spend: float, meta_conv: float, meta_conv_value: float,
              meta_roas: float, meta_cpa: float,
              meta_spend_wow: Optional[float], meta_conv_wow: Optional[float],
              meta_roas_wow: Optional[float], meta_cpa_wow: Optional[float],
              goog_spend: float, goog_purch: float, goog_purch_value: float,
              goog_roas: float, goog_cpa: float,
              goog_spend_wow: Optional[float], goog_purch_wow: Optional[float],
              goog_roas_wow: Optional[float], goog_cpa_wow: Optional[float],
              section_label: str = "PERFORMANCE POR CANAL") -> str:

    meta = _channel_card("Meta Ads", _C["meta_blue"], [
        ("Investimento",   _brl(meta_spend),      meta_spend_wow,  True),
        ("Compras",        _num_conv(meta_conv),   meta_conv_wow,   False),
        ("Receita atrib.", _brl(meta_conv_value),  None,            False),
        ("ROAS",           _x(meta_roas),          meta_roas_wow,   False),
        ("CPA",            _brl(meta_cpa),         meta_cpa_wow,    True),
    ])
    goog = _channel_card("Google Ads", _C["goog_green"], [
        ("Investimento",      _brl(goog_spend),       goog_spend_wow,  True),
        ("Compras atribu&#237;das", _num_conv(goog_purch), goog_purch_wow, False),
        ("Receita ecomm.",    _brl(goog_purch_value), None,            False),
        ("ROAS ecommerce",    _x(goog_roas),          goog_roas_wow,   False),
        ("CPA ecommerce",     _brl(goog_cpa),         goog_cpa_wow,    True),
    ])

    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label(section_label)}
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td width="50%" valign="top" style="padding-right:8px;">{meta}</td>
        <td width="50%" valign="top" style="padding-left:8px;">{goog}</td>
      </tr>
    </table>
  </td>
</tr>"""


def _month_pacing_row(facts_with_targets: list[dict],
                      period_start: str, period_end: str) -> str:
    """
    Compact RITMO DO MÊS block.
    Returns empty string if no target data is available.
    """
    # Look for MTD revenue pacing fact (added by scheduler from core_client_truth)
    rev_fact = next(
        (f for f in facts_with_targets
         if f.get("metric_key") == "month_to_date_revenue" and f.get("target") is not None),
        None,
    )
    if rev_fact is None:
        return ""

    import datetime as dt
    try:
        s = dt.date.fromisoformat(period_start)
        e = dt.date.fromisoformat(period_end)
        # Days elapsed = end day of the period
        days_elapsed = e.day
        import calendar
        days_in_month = calendar.monthrange(e.year, e.month)[1]
        pct_elapsed   = days_elapsed / days_in_month * 100
    except Exception:
        return ""

    revenue   = rev_fact.get("value") or 0
    target    = rev_fact.get("target") or 0
    if target == 0:
        return ""

    realized_pct = revenue / target * 100
    projected    = revenue / (days_elapsed / days_in_month) if days_elapsed > 0 else 0

    proj_color = _C["positive"] if projected >= target else _C["negative"]
    bar_realized = min(int(realized_pct), 100)
    bar_expected = min(int(pct_elapsed), 100)

    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:20px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("RITMO DO M&#202;S")}
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td width="50%" valign="top" style="padding-right:8px;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:14px 16px;">
                <div style="font-size:10px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;margin-bottom:8px;">REALIZADO</div>
                <div style="font-size:18px;font-weight:700;color:{_C['text_1']};font-family:Arial,Helvetica,sans-serif;">{_brl(revenue)}</div>
                <div style="font-size:12px;color:{_C['text_2']};margin-top:4px;font-family:Arial,Helvetica,sans-serif;">{realized_pct:.0f}% da meta &nbsp;&middot;&nbsp; esperado {pct_elapsed:.0f}%</div>
                <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:10px;">
                  <tr><td bgcolor="{_C['sep']}" height="6" style="border-radius:3px;background-color:{_C['sep']};font-size:0;line-height:0;">
                    <table cellpadding="0" cellspacing="0" border="0" width="{bar_realized}%"><tr>
                      <td bgcolor="{_C['accent']}" height="6" style="background-color:{_C['accent']};border-radius:3px;font-size:0;line-height:0;"></td>
                    </tr></table>
                  </td></tr>
                </table>
              </td>
            </tr>
          </table>
        </td>
        <td width="50%" valign="top" style="padding-left:8px;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:14px 16px;">
                <div style="font-size:10px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;margin-bottom:8px;">PROJE&#199;&#195;O</div>
                <div style="font-size:18px;font-weight:700;color:{proj_color};font-family:Arial,Helvetica,sans-serif;">{_brl(projected)}</div>
                <div style="font-size:12px;color:{_C['text_2']};margin-top:4px;font-family:Arial,Helvetica,sans-serif;">Meta: {_brl(target)}</div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </td>
</tr>"""


def _monthly_target_block(facts_list: list[dict]) -> str:
    """META DO MÊS — H8 closed-month realizado vs target."""
    rev_fact = next(
        (f for f in facts_list
         if f.get("metric_key") == "revenue_business" and f.get("target") is not None),
        None,
    )
    if rev_fact is None:
        return ""
    revenue = rev_fact.get("value") or 0
    target  = rev_fact.get("target") or 0
    if target == 0:
        return ""
    realized_pct = revenue / target * 100
    achieved     = revenue >= target
    status_color = _C["positive"] if achieved else _C["negative"]
    status_txt   = "META SUPERADA" if realized_pct > 101 else ("META ATINGIDA" if achieved else "ABAIXO DA META")
    bar_pct      = min(int(realized_pct), 100)
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:20px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("META DO M&#202;S")}
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td width="50%" valign="top" style="padding-right:8px;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:14px 16px;">
                <div style="font-size:10px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;margin-bottom:8px;">REALIZADO</div>
                <div style="font-size:18px;font-weight:700;color:{_C['text_1']};font-family:Arial,Helvetica,sans-serif;">{_brl(revenue)}</div>
                <div style="font-size:12px;color:{_C['text_2']};margin-top:4px;font-family:Arial,Helvetica,sans-serif;">{realized_pct:.0f}% da meta</div>
                <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:10px;">
                  <tr><td bgcolor="{_C['sep']}" height="6" style="border-radius:3px;background-color:{_C['sep']};font-size:0;line-height:0;">
                    <table cellpadding="0" cellspacing="0" border="0" width="{bar_pct}%"><tr>
                      <td bgcolor="{status_color}" height="6" style="background-color:{status_color};border-radius:3px;font-size:0;line-height:0;"></td>
                    </tr></table>
                  </td></tr>
                </table>
              </td>
            </tr>
          </table>
        </td>
        <td width="50%" valign="top" style="padding-left:8px;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:14px 16px;">
                <div style="font-size:10px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;margin-bottom:8px;">STATUS</div>
                <div style="font-size:18px;font-weight:700;color:{status_color};font-family:Arial,Helvetica,sans-serif;">{status_txt}</div>
                <div style="font-size:12px;color:{_C['text_2']};margin-top:4px;font-family:Arial,Helvetica,sans-serif;">Meta: {_brl(target)}</div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </td>
</tr>"""


def _insights(diagnosis: list[dict]) -> str:
    scope_colors = {
        "BUSINESS":    _C["accent"],
        "META":        "#4AA3E8",
        "GOOGLE":      "#4ACC7A",
        "MEASUREMENT": "#A78BFA",
    }
    cards_html = ""
    for d in diagnosis[:3]:
        scope  = d.get("scope", "")
        title  = _clean(d.get("finding", d.get("observation", "")))
        interp = _clean(d.get("interpretation", d.get("diagnosis", "")))
        sc     = scope_colors.get(scope, _C["accent"])
        cards_html += (
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;">'
            f'<tr><td bgcolor="{_C["card_hi"]}" style="background-color:{_C["card_hi"]};border-radius:0 8px 8px 0;'
            f'border-left:3px solid {sc};border-top:1px solid {_C["border"]};border-right:1px solid {_C["border"]};border-bottom:1px solid {_C["border"]};padding:14px 16px;">'
            f'<div style="font-size:10px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:{sc};font-family:Arial,Helvetica,sans-serif;margin-bottom:7px;">{scope}</div>'
            f'<div style="font-size:13px;font-weight:700;color:{_C["text_1"]};font-family:Arial,Helvetica,sans-serif;margin-bottom:6px;line-height:1.3;">{title}</div>'
            f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;line-height:1.6;">{interp}</div>'
            f'</td></tr></table>'
        )
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("LEITURA DA SEMANA")}
    {cards_html}
  </td>
</tr>"""


def _insights_monthly(diagnosis: list[dict]) -> str:
    """Monthly variant — same layout, different section label."""
    scope_colors = {
        "BUSINESS":    _C["accent"],
        "META":        "#4AA3E8",
        "GOOGLE":      "#4ACC7A",
        "MEASUREMENT": "#A78BFA",
    }
    cards_html = ""
    for d in diagnosis[:3]:
        scope  = d.get("scope") or ""
        title  = _clean(d.get("finding", d.get("observation", "")))
        interp = _clean(d.get("interpretation", d.get("diagnosis", "")))
        if not title and not interp:
            continue
        sc     = scope_colors.get(scope, _C["accent"])
        scope_div = (
            f'<div style="font-size:10px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:{sc};font-family:Arial,Helvetica,sans-serif;margin-bottom:7px;">{scope}</div>'
            if scope else ""
        )
        cards_html += (
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;">'
            f'<tr><td bgcolor="{_C["card_hi"]}" style="background-color:{_C["card_hi"]};border-radius:0 8px 8px 0;'
            f'border-left:3px solid {sc};border-top:1px solid {_C["border"]};border-right:1px solid {_C["border"]};border-bottom:1px solid {_C["border"]};padding:14px 16px;">'
            f'{scope_div}'
            f'<div style="font-size:13px;font-weight:700;color:{_C["text_1"]};font-family:Arial,Helvetica,sans-serif;margin-bottom:6px;line-height:1.3;">{title}</div>'
            f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;line-height:1.6;">{interp}</div>'
            f'</td></tr></table>'
        )
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("DIAGN&#211;STICO DO M&#202;S")}
    {cards_html}
  </td>
</tr>"""


def _priorities(recommendations: list[dict], priorities: list[dict]) -> str:
    items = priorities[:3] if priorities else recommendations[:3]
    rows_html = ""
    for i, item in enumerate(items, 1):
        focus  = _clean(item.get("focus", item.get("action", item.get("recommendation", ""))))
        detail = _clean(item.get("action", item.get("decision_gate", "")))
        if detail == focus:
            detail = _clean(item.get("rationale", item.get("evidence", "")))
        rows_html += (
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:12px;">'
            f'<tr>'
            f'<td width="32" valign="top" style="padding-right:14px;padding-top:2px;">'
            f'<table cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td width="28" height="28" bgcolor="{_C["accent"]}" style="background-color:{_C["accent"]};border-radius:50%;width:28px;height:28px;text-align:center;vertical-align:middle;">'
            f'<div style="font-size:13px;font-weight:700;color:{_C["cta_text"]};font-family:Arial,Helvetica,sans-serif;line-height:28px;">{i}</div>'
            f'</td></tr></table>'
            f'</td>'
            f'<td valign="top">'
            f'<div style="font-size:13px;font-weight:700;color:{_C["text_1"]};font-family:Arial,Helvetica,sans-serif;line-height:1.4;margin-bottom:4px;">{focus}</div>'
            + (
                f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;line-height:1.55;">{detail}</div>'
                if detail and detail != focus else ""
            )
            + f'</td></tr></table>'
        )
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("PRIORIDADES")}
    {rows_html}
  </td>
</tr>"""


def _monthly_narrative(summary: str, learnings) -> str:
    if not isinstance(learnings, list):
        learnings = []
    if not summary and not learnings:
        return ""
    sum_html = (
        f'<div style="font-size:13px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;line-height:1.7;margin-bottom:16px;">{_clean(summary)}</div>'
        if summary else ""
    )
    learn_html = ""
    if learnings:
        items = "".join(
            f'<li style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;line-height:1.6;margin-bottom:5px;">'
            f'{_clean(l.get("learning", l) if isinstance(l, dict) else l)}</li>'
            for l in learnings[:4]
        )
        learn_html = (
            f'<div style="font-size:10px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;margin-bottom:8px;">APRENDIZADOS</div>'
            f'<ul style="margin:0;padding-left:18px;">{items}</ul>'
        )
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("RESUMO DO M&#202;S")}
    {sum_html}{learn_html}
  </td>
</tr>"""


# ── H8 expanded section helpers ───────────────────────────────────────────────

def _mix_de_canais_table(
    meta_spend: float, meta_conv: float, meta_value: float,
    meta_roas: float, meta_cpa: float,
    goog_spend: float, goog_purch: float, goog_value: float,
    goog_roas: float, goog_cpa: float,
    total_spend: float,
) -> str:
    ms  = meta_spend  or 0
    gs  = goog_spend  or 0
    tot = total_spend or (ms + gs)
    msh = ms / tot * 100 if tot else 0
    gsh = gs / tot * 100 if tot else 0
    th_s  = (f'style="font-size:9px;font-weight:700;letter-spacing:0.07em;text-transform:uppercase;'
             f'color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;padding:0 4px 8px 0;white-space:nowrap;"')
    thr_s = (f'style="font-size:9px;font-weight:700;letter-spacing:0.07em;text-transform:uppercase;'
             f'color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;padding:0 4px 8px;text-align:right;white-space:nowrap;"')
    header = (
        f'<tr><td {th_s}>CANAL</td><td {thr_s}>INVEST.</td><td {thr_s}>RECEITA ATRIB.</td>'
        f'<td {thr_s}>COMPRAS</td><td {thr_s}>ROAS</td><td {thr_s}>CPA</td><td {thr_s}>PART.</td></tr>'
    )

    def dr(color, name, sp, val, conv, roas, cpa, share):
        td  = (f'style="font-size:11px;color:{_C["text_1"]};font-family:Arial,Helvetica,sans-serif;'
               f'padding:8px 4px;border-bottom:1px solid {_C["sep"]};text-align:right;"')
        td1 = (f'style="font-size:11px;font-weight:700;color:{color};font-family:Arial,Helvetica,sans-serif;'
               f'padding:8px 4px 8px 0;border-bottom:1px solid {_C["sep"]};white-space:nowrap;"')
        tdp = (f'style="font-size:11px;font-weight:700;color:{_C["accent"]};font-family:Arial,Helvetica,sans-serif;'
               f'padding:8px 0 8px 4px;border-bottom:1px solid {_C["sep"]};text-align:right;"')
        return (
            f'<tr><td {td1}>{name}</td><td {td}>{_brl(sp)}</td><td {td}>{_brl(val)}</td>'
            f'<td {td}>{_num_conv(conv)}</td><td {td}>{_x(roas)}</td><td {td}>{_brl(cpa)}</td>'
            f'<td {tdp}>{share:.0f}%</td></tr>'
        )

    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("MIX DE CANAIS")}
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr><td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:16px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          {header}
          {dr(_C['meta_blue'],  "Meta Ads",   meta_spend, meta_value, meta_conv,  meta_roas, meta_cpa, msh)}
          {dr(_C['goog_green'], "Google Ads", goog_spend, goog_value, goog_purch, goog_roas, goog_cpa, gsh)}
        </table>
      </td></tr>
    </table>
  </td>
</tr>"""


def _canal_visao_mensal(
    channel_name: str,
    channel_color: str,
    section_title: str,
    rows: list,
    context_note: str = "",
) -> str:
    """Monthly channel detail table: metric | current | august | MoM.
    rows = [(label, cur_str, aug_str, mom_pct, invert_color)]"""
    th_s  = (f'style="font-size:9px;font-weight:700;letter-spacing:0.07em;text-transform:uppercase;'
             f'color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;padding:0 6px 8px 0;"')
    thr_s = (f'style="font-size:9px;font-weight:700;letter-spacing:0.07em;text-transform:uppercase;'
             f'color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;padding:0 6px 8px;text-align:right;"')
    header = (
        f'<tr><td {th_s}>M&#201;TRICA</td><td {thr_s}>SETEMBRO</td>'
        f'<td {thr_s}>AGOSTO</td><td {thr_s}>MoM</td></tr>'
    )
    rows_html = ""
    for label, cur, aug_v, mom, inv in rows:
        if mom is not None:
            mc = _C["neutral"] if "nvestimento" in label else _delta_color(mom, invert=inv)
            mt = _pct(mom)
        else:
            mc, mt = _C["text_3"], "&#8212;"
        rows_html += (
            f'<tr>'
            f'<td style="font-size:11px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;'
            f'padding:7px 6px 7px 0;border-bottom:1px solid {_C["sep"]};">{label}</td>'
            f'<td align="right" style="font-size:11px;font-weight:600;color:{_C["text_1"]};'
            f'font-family:Arial,Helvetica,sans-serif;padding:7px 6px;border-bottom:1px solid {_C["sep"]};">{cur}</td>'
            f'<td align="right" style="font-size:11px;color:{_C["text_2"]};'
            f'font-family:Arial,Helvetica,sans-serif;padding:7px 6px;border-bottom:1px solid {_C["sep"]};">{aug_v}</td>'
            f'<td align="right" style="font-size:11px;font-weight:700;color:{mc};'
            f'font-family:Arial,Helvetica,sans-serif;padding:7px 0 7px 6px;border-bottom:1px solid {_C["sep"]};">{mt}</td>'
            f'</tr>'
        )
    note_html = (
        f'<tr><td colspan="4" style="font-size:10px;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;'
        f'padding-top:8px;line-height:1.5;">{context_note}</td></tr>'
    ) if context_note else ""
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label(section_title)}
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr><td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:16px;">
        <div style="font-size:12px;font-weight:700;color:{channel_color};font-family:Arial,Helvetica,sans-serif;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:12px;">{channel_name}</div>
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          {header}
          {rows_html}
          {note_html}
        </table>
      </td></tr>
    </table>
  </td>
</tr>"""


def _canonical_campaign_table(envelope: dict, platform: str) -> str:
    """Data binding only: reuse DARK_V4 tokens, never rank diagnostic/rolling rows."""
    from html import escape
    rows = envelope.get('rows') or []
    chosen = set((envelope.get('selection') or {}).get('top_spend_campaign_ids') or [])
    chosen.update((envelope.get('selection') or {}).get('top_efficiency_campaign_ids') or [])
    if not chosen:
        return ''
    color = _C['meta_blue'] if platform == 'meta' else _C['goog_green']
    td = f'font-size:11px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;padding:8px 4px;border-bottom:1px solid {_C["sep"]};'
    columns = ('Campanha / status / tipo', 'Invest.', 'Compras', 'Receita atrib.', 'ROAS', 'CPA')
    header = ''.join(f'<td style="{td}font-weight:700;">{escape(c)}</td>' for c in columns)
    body = ''
    for row in rows:
        if row['campaign_id'] not in chosen:
            continue
        label = ' / '.join(str(row.get(k) or 'não disponível') for k in ('campaign_name', 'status', 'campaign_type') if platform == 'google' or k != 'campaign_type')
        values = (escape(label), _brl(row.get('spend')), _num_conv(row.get('purchases' if platform == 'meta' else 'purchase_conversions')),
                  _brl(row.get('purchase_value')), _x(row.get('roas' if platform == 'meta' else 'ecommerce_roas')),
                  _brl(row.get('cpa' if platform == 'meta' else 'ecommerce_cpa')))
        body += '<tr>' + ''.join(f'<td style="{td}">{v}</td>' for v in values) + '</tr>'
    label = 'META ADS' if platform == 'meta' else 'GOOGLE ADS'
    note = 'Maiores investimentos no período. PROVISIONAL. Status atual na coleta; não representa status histórico.'
    if not (envelope.get('selection') or {}).get('top_efficiency_campaign_ids'):
        note += ' Eficiência não qualificada para escala: regra de volume suficiente não documentada.'
    if platform == 'google':
        note += ' Brand somente quando classificado explicitamente; nomes não comprovam essa segmentação.'
    return f'''<tr><td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
{_section_label(label + ' &#8212; CAMPANHAS')}
<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:16px;">
<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>{header}</tr>{body}</table>
<div style="font-size:10px;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;padding-top:8px;line-height:1.5;">{escape(note)}</div>
</td></tr></table></td></tr>'''


def _limitation_card_campaigns(channel: str, channel_color: str, section_title: str) -> str:
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:20px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label(section_title)}
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr><td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:0 8px 8px 0;border-left:3px solid {_C['neutral']};border-top:1px solid {_C['border']};border-right:1px solid {_C['border']};border-bottom:1px solid {_C['border']};padding:14px 16px;">
        <div style="font-size:12px;font-weight:700;color:{channel_color};font-family:Arial,Helvetica,sans-serif;letter-spacing:0.06em;text-transform:uppercase;margin-bottom:6px;">{channel}</div>
        <div style="font-size:12px;color:{_C['text_2']};font-family:Arial,Helvetica,sans-serif;line-height:1.6;">Granularidade de campanha n&#227;o dispon&#237;vel neste per&#237;odo. O drilldown n&#227;o foi acionado: n&#227;o h&#225; ranking de campanha, conjunto ou an&#250;ncio para o ciclo.</div>
      </td></tr>
    </table>
  </td>
</tr>"""


def _change_digest_mensal(change_digest: dict) -> str:
    if not change_digest:
        return ""
    meta_cd   = change_digest.get("meta") or {}
    goog_cd   = change_digest.get("google") or {}
    causality = change_digest.get("causality", "")
    items_html = (
        f'<div style="font-size:12px;font-weight:700;color:{_C["meta_blue"]};font-family:Arial,Helvetica,sans-serif;'
        f'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:10px;">Meta Ads</div>'
    )
    budgets     = meta_cd.get("budget_changes") or []
    pauses      = meta_cd.get("campaign_pauses") or []
    activations = meta_cd.get("campaign_activations") or []
    interp      = meta_cd.get("interpretation", "")
    if budgets:
        items_html += (
            f'<div style="font-size:10px;font-weight:700;letter-spacing:0.07em;text-transform:uppercase;'
            f'color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;margin-bottom:5px;">OR&#199;AMENTO</div>'
        )
        for b in budgets:
            entity = _clean(b.get("entity", ""))
            items_html += (
                f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;'
                f'padding:4px 0;border-bottom:1px solid {_C["sep"]};">{entity}: '
                f'{b.get("before_display","")} &rarr; {b.get("after_display","")}</div>'
            )
    if pauses:
        items_html += (
            f'<div style="font-size:10px;font-weight:700;letter-spacing:0.07em;text-transform:uppercase;'
            f'color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;margin:10px 0 5px;">CAMPANHAS PAUSADAS</div>'
        )
        for p in list(dict.fromkeys(pauses)):
            items_html += (
                f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;'
                f'padding:4px 0;border-bottom:1px solid {_C["sep"]};">{_clean(p)}</div>'
            )
    if activations:
        items_html += (
            f'<div style="font-size:10px;font-weight:700;letter-spacing:0.07em;text-transform:uppercase;'
            f'color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;margin:10px 0 5px;">CAMPANHAS ATIVADAS</div>'
        )
        for a in list(dict.fromkeys(activations)):
            items_html += (
                f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;'
                f'padding:4px 0;border-bottom:1px solid {_C["sep"]};">{_clean(a)}</div>'
            )
    if interp:
        items_html += (
            f'<div style="font-size:11px;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;'
            f'line-height:1.5;margin-top:10px;">{_clean(interp)}</div>'
        )
    goog_interp = goog_cd.get("interpretation", "")
    items_html += (
        f'<div style="font-size:12px;font-weight:700;color:{_C["goog_green"]};font-family:Arial,Helvetica,sans-serif;'
        f'text-transform:uppercase;letter-spacing:0.06em;margin:16px 0 6px;">Google Ads</div>'
        f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;line-height:1.6;">'
        + _clean(goog_interp or "Nenhum evento registrado no per&#237;odo.")
        + f'</div>'
    )
    if causality:
        items_html += (
            f'<div style="font-size:10px;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;'
            f'line-height:1.5;margin-top:12px;padding-top:10px;border-top:1px solid {_C["sep"]};">'
            + _clean(causality) + f'</div>'
        )
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("CHANGE DIGEST")}
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr><td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:16px;">
        {items_html}
      </td></tr>
    </table>
  </td>
</tr>"""


def _diagnostico_mensal_v2(diagnosis: list) -> str:
    if not diagnosis:
        return ""
    cards_html = ""
    for d in diagnosis[:5]:
        obs    = _clean(d.get("observation", ""))
        diag   = _clean(d.get("diagnosis", ""))
        action = _clean(d.get("recommended_action", ""))
        if not obs and not diag:
            continue
        action_html = (
            f'<div style="font-size:11px;color:{_C["accent"]};font-family:Arial,Helvetica,sans-serif;'
            f'line-height:1.5;margin-top:8px;padding-top:8px;border-top:1px solid {_C["sep"]};">'
            f'<span style="font-weight:700;">A&#231;&#227;o:</span> {action}</div>'
        ) if action else ""
        cards_html += (
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;">'
            f'<tr><td bgcolor="{_C["card_hi"]}" style="background-color:{_C["card_hi"]};border-radius:0 8px 8px 0;'
            f'border-left:3px solid {_C["accent"]};border-top:1px solid {_C["border"]};'
            f'border-right:1px solid {_C["border"]};border-bottom:1px solid {_C["border"]};padding:14px 16px;">'
            f'<div style="font-size:13px;font-weight:700;color:{_C["text_1"]};font-family:Arial,Helvetica,sans-serif;'
            f'margin-bottom:6px;line-height:1.3;">{obs}</div>'
            f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;line-height:1.6;">{diag}</div>'
            f'{action_html}'
            f'</td></tr></table>'
        )
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("DIAGN&#211;STICO DO M&#202;S")}
    {cards_html}
  </td>
</tr>"""


def _plano_proximo_mes(priorities: list, fallback: list) -> str:
    items = (priorities or fallback or [])[:5]
    if not items:
        return ""
    rows_html = ""
    for i, item in enumerate(items, 1):
        action   = _clean(item.get("action", item.get("recommendation", "")))
        priority = item.get("priority", "")
        canal    = _clean(item.get("canal", item.get("channel", "")))
        motivo   = _clean(item.get("motivo", item.get("rationale", item.get("evidence", ""))))
        criterio = _clean(item.get("criterio", item.get("validation_criteria", item.get("decision_gate", ""))))
        badge_color = _C["accent"] if priority in ("P0", "P1") else _C["text_3"]
        badge = (
            f' <span style="font-size:10px;font-weight:700;color:{badge_color};'
            f'background-color:{_C["card_hi"]};border:1px solid {_C["border"]};'
            f'border-radius:4px;padding:2px 6px;font-family:Arial,Helvetica,sans-serif;">{priority}</span>'
        ) if priority else ""
        sub_parts = [p for p in [
            f'Canal: {canal}' if canal else None,
            motivo if motivo else None,
            f'Crit&#233;rio: {criterio}' if criterio and criterio != action else None,
        ] if p]
        sub_html = (
            f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;'
            f'line-height:1.55;margin-top:4px;">' + ' &middot; '.join(sub_parts) + '</div>'
        ) if sub_parts else ""
        rows_html += (
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:12px;">'
            f'<tr>'
            f'<td width="32" valign="top" style="padding-right:14px;padding-top:2px;">'
            f'<table cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td width="28" height="28" bgcolor="{_C["accent"]}" style="background-color:{_C["accent"]};'
            f'border-radius:50%;width:28px;height:28px;text-align:center;vertical-align:middle;">'
            f'<div style="font-size:13px;font-weight:700;color:{_C["cta_text"]};font-family:Arial,Helvetica,sans-serif;'
            f'line-height:28px;">{i}</div>'
            f'</td></tr></table>'
            f'</td>'
            f'<td valign="top">'
            f'<div style="font-size:13px;font-weight:700;color:{_C["text_1"]};font-family:Arial,Helvetica,sans-serif;'
            f'line-height:1.4;">{action}{badge}</div>'
            f'{sub_html}'
            f'</td></tr></table>'
        )
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("PLANO DO PR&#211;XIMO M&#202;S")}
    {rows_html}
  </td>
</tr>"""


def _cta(client_id: str) -> str:
    url = _platform_url(client_id)
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:20px 28px 28px;text-align:center;">
    <table cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;">
      <tr>
        <td bgcolor="{_C['cta_bg']}" style="background-color:{_C['cta_bg']};border-radius:6px;padding:14px 32px;text-align:center;">
          <a href="{url}" style="font-size:14px;font-weight:700;color:{_C['cta_text']};text-decoration:none;font-family:Arial,Helvetica,sans-serif;letter-spacing:0.02em;">Ver an&#225;lise completa na Noro Platform &rarr;</a>
        </td>
      </tr>
    </table>
  </td>
</tr>"""


def _footer() -> str:
    return f"""<tr>
  <td bgcolor="{_C['hdr']}" style="background-color:{_C['hdr']};padding:18px 28px;border-radius:0 0 10px 10px;border-top:1px solid {_C['border']};text-align:center;">
    <div style="font-size:11px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;">NOROLABS &nbsp;|&nbsp; PERFORMANCE &amp; GROWTH</div>
    <div style="font-size:10px;color:{_C['neutral']};margin-top:5px;font-family:Arial,Helvetica,sans-serif;">Dados atribu&#237;dos de m&#237;dia podem sofrer atualiza&#231;&#227;o pelas plataformas. Receita e pedidos seguem a fonte de neg&#243;cio dispon&#237;vel no fechamento.</div>
  </td>
</tr>"""


# ── Data extraction ───────────────────────────────────────────────────────────

def _facts_map(content: dict) -> dict:
    return {f["metric_key"]: f.get("value") for f in content.get("facts", [])}


def _wow_pct(deltas: dict, key: str) -> Optional[float]:
    return deltas.get(key, {}).get("delta_pct")


def _mc_pct(mc: dict, section: str, key: str) -> Optional[float]:
    """Compute MoM % from monthly_comparison nested structure {section: {key: {august, september}}}."""
    item = (mc or {}).get(section, {}).get(key, {})
    aug = item.get("august")
    sep = item.get("september")
    if aug and sep and aug != 0:
        return (sep - aug) / abs(aug) * 100
    return None


# ── Outer wrapper ─────────────────────────────────────────────────────────────

def _wrap(body_rows: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="color-scheme" content="dark">
  <meta name="supported-color-schemes" content="dark">
  <!--[if mso]><noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript><![endif]-->
  <title>NoroLabs Report</title>
</head>
<body style="margin:0;padding:0;background-color:{_C['bg']};-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;" bgcolor="{_C['bg']}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{_C['bg']}" style="background-color:{_C['bg']};">
  <tr>
    <td align="center" style="padding:32px 12px 48px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;border-radius:10px;border:1px solid {_C['border']};overflow:hidden;">
        {body_rows}
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


# ── Public entry points ───────────────────────────────────────────────────────

def render_weekly_email(artifact: dict) -> tuple[str, dict]:
    """
    Render H7 canonical artifact → (html, validation_result).
    validation_result keys: ok, errors, kpis_checked, month_pacing_available.
    """
    content    = artifact.get("content", {})
    facts_list = content.get("facts", [])
    facts      = _facts_map(content)
    al         = content.get("analysis_layer", {})
    client_id  = artifact.get("client_id", "")

    p_start = artifact.get("period_start", "")
    p_end   = artifact.get("period_end", "")

    # ── Validate ───────────────────────────────────────────────────────────
    ok, errors = _validate_kpis(facts)
    if not ok:
        log.error("DATA_PRESENTATION_VALIDATION_FAIL: %s", errors)

    # ── Extract values ─────────────────────────────────────────────────────
    revenue    = facts.get("revenue_business")
    orders_raw = facts.get("orders_count") or 0
    orders     = int(round(orders_raw))
    spend      = facts.get("total_spend")
    mer        = facts.get("mer")
    avg_ticket = (revenue / orders) if revenue and orders else None

    meta_spend  = facts.get("meta_spend")
    meta_conv   = facts.get("meta_conversions")
    meta_value  = facts.get("meta_conversion_value")
    meta_roas   = facts.get("roas_meta")
    meta_cpa    = facts.get("cpa_meta")
    goog_spend  = facts.get("google_spend")
    goog_purch  = facts.get("google_purchase_conversions")
    goog_value  = facts.get("google_purchase_conversion_value")
    goog_roas   = facts.get("google_roas_ecommerce")
    goog_cpa    = facts.get("google_cpa_ecommerce")

    # ── WoW deltas ─────────────────────────────────────────────────────────
    wow_src = (
        al.get("wow_comparison", {}).get("deltas")
        or al.get("executive_summary", {}).get("historical_comparison", {}).get("metrics")
        or {}
    )
    wows = {
        "revenue":    _wow_pct(wow_src, "revenue_business"),
        "orders":     _wow_pct(wow_src, "orders_count"),
        "spend":      _wow_pct(wow_src, "total_spend"),
        "mer":        _wow_pct(wow_src, "mer"),
        "meta_spend": _wow_pct(wow_src, "meta_spend"),
        "meta_conv":  _wow_pct(wow_src, "meta_conversions"),
        "meta_roas":  _wow_pct(wow_src, "roas_meta"),
        "meta_cpa":   _wow_pct(wow_src, "cpa_meta"),
        "goog_spend": _wow_pct(wow_src, "google_spend"),
        "goog_purch": _wow_pct(wow_src, "google_purchase_conversions"),
        "goog_roas":  _wow_pct(wow_src, "google_roas_ecommerce"),
        "goog_cpa":   _wow_pct(wow_src, "google_cpa_ecommerce"),
    }

    diag  = al.get("diagnosis", [])
    recs  = al.get("recommendations", [])
    prios = al.get("priorities_next_week", [])

    pacing_html = _month_pacing_row(facts_list, p_start, p_end)
    month_pacing_available = bool(pacing_html)

    period_label = _fmt_period(p_start, p_end)
    client_name  = _fmt_client(client_id)

    body = "".join([
        _header(client_name, "Performance Semanal", period_label),
        _hero(revenue, orders, wows["revenue"]),
        _kpis_row(orders, wows["orders"], spend, wows["spend"],
                  mer, wows["mer"], avg_ticket),
        _channels(meta_spend, meta_conv, meta_value, meta_roas, meta_cpa,
                  wows["meta_spend"], wows["meta_conv"],
                  wows["meta_roas"], wows["meta_cpa"],
                  goog_spend, goog_purch, goog_value, goog_roas, goog_cpa,
                  wows["goog_spend"], wows["goog_purch"],
                  wows["goog_roas"], wows["goog_cpa"]),
        pacing_html,
        _insights(diag),
        _priorities(recs, prios),
        _cta(client_id),
        _footer(),
    ])

    validation = {
        "ok":                    ok,
        "errors":                errors,
        "kpis_checked":          ["MER", "Meta ROAS", "Meta CPA", "Google ROAS ecom", "Google CPA ecom"],
        "month_pacing_available": month_pacing_available,
    }
    return _wrap(body), validation


def render_monthly_email(artifact: dict) -> tuple[str, dict]:
    """
    Render H8 canonical artifact → (html, validation_result).
    12-section expanded monthly report.
    """
    content    = artifact.get("content", {})
    facts_list = content.get("facts", [])
    facts      = _facts_map(content)
    al         = content.get("analysis_layer", {})
    client_id  = artifact.get("client_id", "")

    p_start = artifact.get("period_start", "")
    p_end   = artifact.get("period_end", "")

    ok, errors = _validate_kpis(facts)
    if not ok:
        log.error("DATA_PRESENTATION_VALIDATION_FAIL: %s", errors)

    revenue    = facts.get("revenue_business")
    orders_raw = facts.get("orders_count") or 0
    orders     = int(round(orders_raw))
    spend      = facts.get("total_spend")
    mer        = facts.get("mer")
    avg_ticket = (revenue / orders) if revenue and orders else None

    meta_spend  = facts.get("meta_spend")
    meta_conv   = facts.get("meta_conversions")
    meta_value  = facts.get("meta_conversion_value")
    meta_roas   = facts.get("roas_meta")
    meta_cpa    = facts.get("cpa_meta")
    goog_spend  = facts.get("google_spend")
    goog_purch  = facts.get("google_purchase_conversions")
    goog_value  = facts.get("google_purchase_conversion_value")
    goog_roas   = facts.get("google_roas_ecommerce")
    goog_cpa    = facts.get("google_cpa_ecommerce")
    goog_total  = facts.get("google_conversions")

    # MoM from monthly_comparison structured dict {section: {key: {august, september}}}
    mc   = al.get("monthly_comparison") or {}
    moms: dict = {
        "revenue":    _mc_pct(mc, "business",        "revenue_business"),
        "orders":     _mc_pct(mc, "business",        "orders_count"),
        "spend":      _mc_pct(mc, "aggregate_media", "total_spend"),
        "mer":        _mc_pct(mc, "aggregate_media", "mer"),
        "meta_spend": _mc_pct(mc, "meta",            "spend"),
        "meta_conv":  _mc_pct(mc, "meta",            "conversions"),
        "goog_spend": _mc_pct(mc, "google",          "spend"),
        "goog_purch": _mc_pct(mc, "google",          "purchase_conversions"),
        "goog_roas":  _mc_pct(mc, "google",          "roas_ecommerce"),
        "goog_cpa":   _mc_pct(mc, "google",          "cpa_ecommerce"),
    }

    # August context for channel detail tables
    meta_ctx = (al.get("meta_analysis") or {}).get("monthly_context", {}).get("august", {})
    goog_ctx = (al.get("google_analysis") or {}).get("monthly_context", {}).get("august", {})

    aug_meta_spend = meta_ctx.get("spend")
    aug_meta_conv  = meta_ctx.get("conversions")
    aug_meta_value = meta_ctx.get("conversion_value")
    aug_meta_roas  = meta_ctx.get("roas")
    aug_meta_cpa   = meta_ctx.get("cpa")

    aug_goog_spend = goog_ctx.get("spend")
    aug_goog_purch = goog_ctx.get("purchase_conversions")
    aug_goog_roas  = goog_ctx.get("roas_ecommerce")
    aug_goog_cpa   = goog_ctx.get("cpa_ecommerce")
    aug_goog_total = goog_ctx.get("total_conversions")

    def _mom(cur: Optional[float], aug: Optional[float]) -> Optional[float]:
        if cur and aug and aug != 0:
            return (cur - aug) / abs(aug) * 100
        return None

    moms["meta_roas"] = _mom(meta_roas, aug_meta_roas)
    moms["meta_cpa"]  = _mom(meta_cpa, aug_meta_cpa)

    aug_goog_value = (aug_goog_roas * aug_goog_spend) if aug_goog_roas and aug_goog_spend else None

    # Channel rows
    meta_rows = [
        ("Investimento",           _brl(meta_spend),  _brl(aug_meta_spend),                                       moms.get("meta_spend"), True),
        ("Compras",                _num_conv(meta_conv), _num_conv(aug_meta_conv) if aug_meta_conv else "&#8212;", moms.get("meta_conv"),  False),
        ("Receita atribu&#237;da", _brl(meta_value),  _brl(aug_meta_value),                                       _mom(meta_value, aug_meta_value), False),
        ("ROAS",                   _x(meta_roas),     _x(aug_meta_roas),                                          moms.get("meta_roas"), False),
        ("CPA",                    _brl(meta_cpa),    _brl(aug_meta_cpa),                                         moms.get("meta_cpa"), True),
    ]
    goog_rows = [
        ("Investimento",             _brl(goog_spend),   _brl(aug_goog_spend),                                        moms.get("goog_spend"), True),
        ("Compras ecommerce",        _num_conv(goog_purch), _num_conv(aug_goog_purch) if aug_goog_purch else "&#8212;", moms.get("goog_purch"), False),
        ("Receita ecommerce",        _brl(goog_value),   _brl(aug_goog_value),                                        _mom(goog_value, aug_goog_value), False),
        ("ROAS ecommerce",           _x(goog_roas),      _x(aug_goog_roas),                                           moms.get("goog_roas"), False),
        ("CPA ecommerce",            _brl(goog_cpa),     _brl(aug_goog_cpa),                                          moms.get("goog_cpa"), True),
        ("Convers&#245;es totais*",  _num_conv(goog_total), _num_conv(aug_goog_total) if aug_goog_total else "&#8212;", _mom(goog_total, aug_goog_total), False),
    ]

    # Campaign availability
    from .h8_evidence_guard import bound_context,evaluate,campaign_availability
    evidence_context = bound_context(client_id, {'start':p_start,'end':p_end}, content)
    h8_gate = evaluate({**al,'priorities':al.get('priorities_next_week') or []}, evidence_context)
    campaigns = evidence_context['campaign_performance']
    meta_campaign_html = _canonical_campaign_table(campaigns.get('meta') or {}, 'meta') if campaign_availability(evidence_context,'meta') == 'AVAILABLE' else ''
    google_campaign_html = _canonical_campaign_table(campaigns.get('google') or {}, 'google') if campaign_availability(evidence_context,'google') == 'AVAILABLE' else ''
    meta_camp_avail = bool(meta_campaign_html)
    goog_camp_avail = bool(google_campaign_html)

    diag          = al.get("diagnosis", [])
    prios         = al.get("next_month_priorities", al.get("priorities_next_week", []))
    recs          = al.get("recommendations", [])
    change_digest = al.get("change_digest_section") or {}

    target_html  = _monthly_target_block(facts_list)
    period_label = _fmt_period_monthly(p_start, p_end)
    client_name  = _fmt_client(client_id)

    body = "".join([
        _header(client_name, "Performance Mensal", period_label),
        # 1. Visão executiva
        _hero_monthly(revenue, orders, moms.get("revenue")),
        _kpis_row_monthly(orders, moms.get("orders"), spend, moms.get("spend"),
                          mer, moms.get("mer"), avg_ticket),
        # 2. Meta vs realizado (ritmo / fechamento)
        target_html,
        # 3. Mix de canais
        _mix_de_canais_table(meta_spend, meta_conv, meta_value, meta_roas, meta_cpa,
                              goog_spend, goog_purch, goog_value, goog_roas, goog_cpa, spend),
        # 4. Meta Ads — Visão do mês
        _canal_visao_mensal(
            "Meta Ads", _C["meta_blue"], "META ADS &#8212; VIS&#195;O DO M&#202;S", meta_rows,
            "* Receita e compras atribu&#237;das pela plataforma; n&#227;o somar &#224; receita Shopify.",
        ),
        # 5. Meta Ads — Campanhas
        meta_campaign_html if meta_camp_avail else _limitation_card_campaigns(
            "Meta Ads", _C["meta_blue"], "META ADS &#8212; CAMPANHAS",
        ),
        # 6. Google Ads — Visão do mês
        _canal_visao_mensal(
            "Google Ads", _C["goog_green"], "GOOGLE ADS &#8212; VIS&#195;O DO M&#202;S", goog_rows,
            "* Convers&#245;es totais incluem a&#231;&#245;es al&#233;m de compras; usar KPIs de ecommerce para decis&#245;es.",
        ),
        # 7. Google Ads — Campanhas
        google_campaign_html if goog_camp_avail else _limitation_card_campaigns(
            "Google Ads", _C["goog_green"], "GOOGLE ADS &#8212; CAMPANHAS",
        ),
        # 8. Change Digest
        _change_digest_mensal(change_digest),
        # 9. Diagnóstico do mês (up to 5)
        _diagnostico_mensal_v2(diag),
        # 10. Plano do próximo mês
        _plano_proximo_mes(prios, recs),
        _cta(client_id),
        _footer(),
    ])

    validation = {
        "ok":                      ok and h8_gate['H8_CLIENT_READY']=='YES',
        "h8_evidence_quality_gate": h8_gate,
        "errors":                  errors,
        "kpis_checked":            ["MER", "Meta ROAS", "Meta CPA", "Google ROAS ecom", "Google CPA ecom"],
        "month_pacing_available":  bool(target_html),
        "meta_campaigns_rendered": meta_camp_avail,
        "goog_campaigns_rendered": goog_camp_avail,
        "meta_creatives_rendered": False,
        "change_digest_rendered":  bool(change_digest),
    }
    return _wrap(body), validation
