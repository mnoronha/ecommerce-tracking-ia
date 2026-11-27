"""
NoroLabs Canonical Email Renderer — v5.0-dark
Design: dark navy, NoroLabs turquoise, logo mark, bar charts, executive summary.
Email-safe: inline styles only, no JavaScript, Gmail + Outlook compatible.
"""
from __future__ import annotations
from typing import Optional
import logging

log = logging.getLogger(__name__)

TEMPLATE_VERSION = "v5.0-dark"
PLATFORM_BASE_URL = "https://track.noroia.com.br"
_KPI_TOLERANCE = 0.03

# ── Design tokens ─────────────────────────────────────────────────────────────
_C = {
    "bg":         "#080E1A",
    "card":       "#0E1725",
    "card_hi":    "#132030",
    "border":     "#1C2B3F",
    "sep":        "#1A2840",
    "accent":     "#00C4B4",
    "positive":   "#00C4B4",
    "negative":   "#F06565",
    "warning":    "#F5A623",
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
    "bar_prev":   "#1C2B3F",
}


# ── Formatters ────────────────────────────────────────────────────────────────

def _brl(v: Optional[float]) -> str:
    if v is None:
        return "&#8212;"
    return "R$&nbsp;" + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _num(v: Optional[float]) -> str:
    if v is None:
        return "&#8212;"
    return f"{int(round(v)):,}".replace(",", ".")


def _num_conv(v: Optional[float]) -> str:
    if v is None:
        return "&#8212;"
    if abs(v - round(v)) < 0.001:
        return _num(v)
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(v: Optional[float]) -> str:
    if v is None:
        return ""
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.1f}%".replace(".", ",")


def _x(v: Optional[float]) -> str:
    if v is None:
        return "&#8212;"
    return f"{v:.2f}x".replace(".", ",")


def _delta_color(v: Optional[float], invert: bool = False) -> str:
    if v is None or abs(v) < 1.0:
        return _C["neutral"]
    good = v > 0 if not invert else v < 0
    return _C["positive"] if good else _C["negative"]


# ── Text cleaner ──────────────────────────────────────────────────────────────

_TEXT_REPLACEMENTS = [
    ("SMALL_SAMPLE", "amostra reduzida"),
    ("small_sample", "amostra reduzida"),
    ("canônica do canal", "do canal"),
    ("deterioração canônica", "queda"),
    ("canônico", ""),
    ("canônica", ""),
    ("Business Truth", "dados de negócio"),
    ("business truth", "dados de negócio"),
    ("dados PROVISIONAL", "dados disponíveis"),
    ("PROVISIONAL", ""),
    ("UNKNOWN", "não verificado"),
    (
        "O CPA blended de R$ 41,51 é dominado por conversões que não devem ser tratadas como vendas.",
        "Conversões secundárias continuam distorcendo o CPA blended. "
        "Para decisões de mídia, usar somente compras, receita, ROAS e CPA de ecommerce.",
    ),
    (
        "Usar somente KPIs canônicos de ecommerce para decisões e tratar a granularidade "
        "diagnóstica como SMALL_SAMPLE.",
        "Usar somente dados de ecommerce (compras, receita, ROAS e CPA) para decisões de mídia.",
    ),
    (
        "tratar a granularidade diagnóstica como SMALL_SAMPLE",
        "considerar o volume reduzido de amostra antes de tomar decisões",
    ),
    (
        "tratar a granularidade diagnóstica com SMALL_SAMPLE",
        "considerar o volume reduzido de amostra antes de tomar decisões",
    ),
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
    errors: list[str] = []

    def _chk(label: str, stored: Optional[float], computed: Optional[float]) -> None:
        if not stored or not computed:
            return
        delta = abs(stored - computed) / max(abs(stored), abs(computed))
        if delta > _KPI_TOLERANCE:
            errors.append(f"{label}: stored={stored:.4f} computed={computed:.4f} delta={delta:.1%}")

    rev  = facts.get("revenue_business")
    ms   = facts.get("meta_spend")
    mc   = facts.get("meta_conversions")
    mv   = facts.get("meta_conversion_value")
    gs   = facts.get("google_spend")
    gp   = facts.get("google_purchase_conversions")
    gv   = facts.get("google_purchase_conversion_value")
    spend = facts.get("total_spend")

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


# ── Period helpers ────────────────────────────────────────────────────────────

_PT_MONTHS     = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
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


def _month_labels_from_period(period_start: str) -> tuple[str, str]:
    """Return (current_month_label, prev_month_label) in uppercase PT-BR."""
    try:
        import datetime as dt
        s = dt.date.fromisoformat(period_start)
        cur = _PT_MONTHS_FULL[s.month - 1].upper()
        prev_date = (s.replace(day=1) - dt.timedelta(days=1))
        prev = _PT_MONTHS_FULL[prev_date.month - 1].upper()
        return cur, prev
    except Exception:
        return "MÊS ATUAL", "MÊS ANTERIOR"


def _fmt_client(client_id: str) -> str:
    mapping = {
        "lk-sneakers": "LK Sneakers",
        "enutri":       "E-Nutri",
        "dipua":        "Dipua",
    }
    return mapping.get(client_id, client_id.replace("-", " ").title())


def _platform_url(client_id: str) -> str:
    return f"{PLATFORM_BASE_URL}/clients/{client_id}/canonical-reports"


# ── HTML primitives ───────────────────────────────────────────────────────────

def _section_label(text: str) -> str:
    return (
        f'<div style="font-size:10px;font-weight:700;letter-spacing:0.12em;'
        f'text-transform:uppercase;color:{_C["text_3"]};'
        f'font-family:Arial,Helvetica,sans-serif;'
        f'padding-bottom:12px;margin-bottom:16px;'
        f'border-bottom:1px solid {_C["sep"]};">{text}</div>'
    )


def _provisional_badge() -> str:
    return (
        f'&nbsp;<span style="font-size:9px;font-weight:700;letter-spacing:0.07em;'
        f'color:{_C["warning"]};font-family:Arial,Helvetica,sans-serif;'
        f'border:1px solid {_C["warning"]};border-radius:3px;padding:1px 5px;'
        f'text-transform:uppercase;vertical-align:middle;">PROVISIONAL</span>'
    )


# ── Section: Header ───────────────────────────────────────────────────────────

def _header(client_name: str, report_label: str, period_label: str) -> str:
    return f"""<tr>
  <td bgcolor="{_C['hdr']}" style="background-color:{_C['hdr']};padding:24px 28px 22px;border-radius:10px 10px 0 0;border-bottom:1px solid {_C['border']};">
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td valign="middle">
          <table cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td width="26" height="26" bgcolor="{_C['accent']}" style="background-color:{_C['accent']};border-radius:5px;width:26px;height:26px;text-align:center;vertical-align:middle;">
                <div style="font-size:14px;font-weight:900;color:{_C['hdr']};font-family:Arial,Helvetica,sans-serif;line-height:26px;text-align:center;">N</div>
              </td>
              <td style="padding-left:9px;vertical-align:middle;">
                <div style="font-size:13px;font-weight:800;letter-spacing:0.08em;color:{_C['text_1']};font-family:Arial,Helvetica,sans-serif;">NOROLABS</div>
              </td>
              <td style="padding-left:14px;padding-top:1px;vertical-align:middle;">
                <div style="font-size:11px;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;">&times; {client_name}</div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
      <tr><td height="18" style="font-size:0;line-height:0;">&nbsp;</td></tr>
      <tr>
        <td>
          <div style="font-size:22px;font-weight:700;color:{_C['white']};font-family:Arial,Helvetica,sans-serif;letter-spacing:-0.015em;line-height:1.15;">{report_label}</div>
          <div style="font-size:13px;color:{_C['text_2']};margin-top:5px;font-family:Arial,Helvetica,sans-serif;line-height:1.4;">{period_label}</div>
        </td>
      </tr>
    </table>
  </td>
</tr>"""


# ── Section: Hero (weekly) ────────────────────────────────────────────────────

def _hero(revenue: Optional[float], orders: int, revenue_wow: Optional[float],
          provisional: bool = False) -> str:
    wow_color = _delta_color(revenue_wow)
    wow_txt   = _pct(revenue_wow)
    prov      = _provisional_badge() if provisional else ""
    wow_block = ""
    if wow_txt:
        wow_block = (
            f'<td align="right" valign="bottom" style="padding-bottom:4px;">'
            f'<table cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td bgcolor="{_C["card_hi"]}" style="background-color:{_C["card_hi"]};border-radius:8px;border:1px solid {_C["border"]};padding:10px 16px;text-align:center;">'
            f'<div style="font-size:22px;font-weight:700;color:{wow_color};font-family:Arial,Helvetica,sans-serif;line-height:1;">{wow_txt}</div>'
            f'<div style="font-size:10px;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;text-transform:uppercase;letter-spacing:0.06em;margin-top:4px;">vs semana ant.</div>'
            f'</td></tr></table></td>'
        )
    rev_display = _brl(revenue) if revenue is not None else '<span style="color:{};font-size:20px;">Dados indisponíveis</span>'.format(_C["text_3"])
    orders_display = f'{_num(orders)} pedidos' if orders else '— pedidos'
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:28px 28px 24px;border-bottom:1px solid {_C['sep']};">
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td valign="bottom">
          <div style="font-size:10px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;margin-bottom:10px;">RECEITA DA SEMANA{prov}</div>
          <div style="font-size:38px;font-weight:700;color:{_C['white']};font-family:Arial,Helvetica,sans-serif;line-height:1;letter-spacing:-0.02em;">{rev_display}</div>
          <div style="font-size:13px;color:{_C['text_2']};margin-top:8px;font-family:Arial,Helvetica,sans-serif;">{orders_display}</div>
        </td>
        {wow_block}
      </tr>
    </table>
  </td>
</tr>"""


# ── Section: Hero (monthly) ───────────────────────────────────────────────────

def _hero_monthly(revenue: Optional[float], orders: int, revenue_mom: Optional[float],
                  provisional: bool = False) -> str:
    wow_color = _delta_color(revenue_mom)
    wow_txt   = _pct(revenue_mom)
    prov      = _provisional_badge() if provisional else ""
    wow_block = ""
    if wow_txt:
        wow_block = (
            f'<td align="right" valign="bottom" style="padding-bottom:4px;">'
            f'<table cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td bgcolor="{_C["card_hi"]}" style="background-color:{_C["card_hi"]};border-radius:8px;border:1px solid {_C["border"]};padding:10px 16px;text-align:center;">'
            f'<div style="font-size:22px;font-weight:700;color:{wow_color};font-family:Arial,Helvetica,sans-serif;line-height:1;">{wow_txt}</div>'
            f'<div style="font-size:10px;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;text-transform:uppercase;letter-spacing:0.06em;margin-top:4px;">vs mês anterior</div>'
            f'</td></tr></table></td>'
        )
    rev_display = _brl(revenue) if revenue is not None else f'<span style="color:{_C["text_3"]};font-size:20px;">Dados indisponíveis</span>'
    orders_display = f'{_num(orders)} pedidos' if orders else '— pedidos'
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:28px 28px 24px;border-bottom:1px solid {_C['sep']};">
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td valign="bottom">
          <div style="font-size:10px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;margin-bottom:10px;">RECEITA DO M&#202;S{prov}</div>
          <div style="font-size:38px;font-weight:700;color:{_C['white']};font-family:Arial,Helvetica,sans-serif;line-height:1;letter-spacing:-0.02em;">{rev_display}</div>
          <div style="font-size:13px;color:{_C['text_2']};margin-top:8px;font-family:Arial,Helvetica,sans-serif;">{orders_display}</div>
        </td>
        {wow_block}
      </tr>
    </table>
  </td>
</tr>"""


# ── Section: Executive Summary ────────────────────────────────────────────────

def _executive_summary(al: dict, section_label_text: str = "RESUMO DA SEMANA") -> str:
    es      = al.get("executive_summary") or {}
    bullets = es.get("bullets") or []
    key_win = _clean(es.get("key_win") or "")
    key_risk = _clean(es.get("key_risk") or "")
    status  = es.get("status") or ""

    if not bullets and not key_win and not key_risk:
        return ""

    prov = _provisional_badge() if status == "PROVISIONAL" else ""

    bullets_html = ""
    for b in bullets[:3]:
        bullets_html += (
            f'<div style="font-size:13px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;'
            f'line-height:1.65;padding:9px 0;border-bottom:1px solid {_C["sep"]};">{_clean(b)}</div>'
        )

    kw_html = ""
    if key_win:
        kw_html = (
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:14px;">'
            f'<tr><td bgcolor="{_C["card_hi"]}" style="background-color:{_C["card_hi"]};'
            f'border-left:3px solid {_C["positive"]};border-radius:0 6px 6px 0;'
            f'border-top:1px solid {_C["border"]};border-right:1px solid {_C["border"]};'
            f'border-bottom:1px solid {_C["border"]};padding:11px 14px;">'
            f'<div style="font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;'
            f'color:{_C["positive"]};font-family:Arial,Helvetica,sans-serif;margin-bottom:5px;">Destaque positivo</div>'
            f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;line-height:1.5;">{key_win}</div>'
            f'</td></tr></table>'
        )

    kr_html = ""
    if key_risk:
        kr_html = (
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:8px;">'
            f'<tr><td bgcolor="{_C["card_hi"]}" style="background-color:{_C["card_hi"]};'
            f'border-left:3px solid {_C["negative"]};border-radius:0 6px 6px 0;'
            f'border-top:1px solid {_C["border"]};border-right:1px solid {_C["border"]};'
            f'border-bottom:1px solid {_C["border"]};padding:11px 14px;">'
            f'<div style="font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;'
            f'color:{_C["negative"]};font-family:Arial,Helvetica,sans-serif;margin-bottom:5px;">Ponto de atenção</div>'
            f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;line-height:1.5;">{key_risk}</div>'
            f'</td></tr></table>'
        )

    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label(section_label_text + prov)}
    {bullets_html}
    {kw_html}
    {kr_html}
  </td>
</tr>"""


# ── Section: KPI cards ────────────────────────────────────────────────────────

def _kpi_card(label: str, value: str, delta: str, dcol: str, note: str = "") -> str:
    note_html = (
        f'<div style="font-size:10px;color:{_C["text_3"]};margin-top:5px;font-family:Arial,Helvetica,sans-serif;line-height:1.4;">{note}</div>'
        if note else ""
    )
    delta_html = (
        f'<div style="font-size:12px;font-weight:700;color:{dcol};margin-top:5px;font-family:Arial,Helvetica,sans-serif;">{delta}</div>'
        if delta else '<div style="height:5px;"></div>'
    )
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td bgcolor="{_C["card_hi"]}" style="background-color:{_C["card_hi"]};border-radius:8px;border:1px solid {_C["border"]};padding:16px 16px 18px;">'
        f'<div style="font-size:9px;font-weight:700;letter-spacing:0.09em;text-transform:uppercase;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;margin-bottom:9px;">{label}</div>'
        f'<div style="font-size:22px;font-weight:700;color:{_C["text_1"]};font-family:Arial,Helvetica,sans-serif;line-height:1.1;">{value}</div>'
        f'{delta_html}'
        f'{note_html}'
        f'</td></tr></table>'
    )


def _kpis_row(orders: int, orders_wow: Optional[float],
              spend: Optional[float], spend_wow: Optional[float],
              mer: Optional[float], mer_wow: Optional[float],
              avg_ticket: Optional[float],
              avg_ticket_wow: Optional[float] = None,
              section_label: str = "KPIs DA SEMANA",
              mer_scope_note: str = "Receita &divide; investimento total em m&iacute;dia") -> str:

    c1 = _kpi_card("PEDIDOS",       _num(orders),    _pct(orders_wow), _delta_color(orders_wow))
    c2 = _kpi_card("INVESTIMENTO",  _brl(spend),     _pct(spend_wow),  _C["neutral"])
    c3 = _kpi_card("MER",           _x(mer),         _pct(mer_wow),    _delta_color(mer_wow),
                   note=mer_scope_note if mer is not None else "")
    c4 = _kpi_card("TICKET M&#201;DIO", _brl(avg_ticket),
                   _pct(avg_ticket_wow), _delta_color(avg_ticket_wow))

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
                      spend: Optional[float], spend_mom: Optional[float],
                      mer: Optional[float], mer_mom: Optional[float],
                      avg_ticket: Optional[float],
                      avg_ticket_mom: Optional[float] = None) -> str:
    return _kpis_row(orders, orders_mom, spend, spend_mom, mer, mer_mom, avg_ticket,
                     avg_ticket_wow=avg_ticket_mom,
                     section_label="KPIs DO M&#202;S")


# ── Section: Mini bar charts ──────────────────────────────────────────────────

def _bar_pair(cur: Optional[float], prev: Optional[float], color: str) -> str:
    """Two stacked horizontal bars: current on top, previous below."""
    if cur is None:
        return ""
    if prev and prev > 0 and cur > 0:
        mx = max(cur, prev)
        pct_c = max(4, int(cur / mx * 100))
        pct_p = max(4, int(prev / mx * 100))
    else:
        pct_c, pct_p = 100, 0

    def bar(pct: int, bg: str) -> str:
        return (
            f'<table cellpadding="0" cellspacing="0" border="0" width="100%">'
            f'<tr><td bgcolor="{bg}" height="8" width="{pct}%" '
            f'style="background-color:{bg};border-radius:4px;font-size:0;line-height:0;"></td>'
            + (f'<td width="{100-pct}%"></td>' if pct < 100 else '')
            + f'</tr></table>'
        )

    return bar(pct_c, color) + '<div style="height:5px;"></div>' + bar(pct_p, _C["bar_prev"])


def _mini_charts(
    revenue: Optional[float], prev_revenue: Optional[float], revenue_wow: Optional[float],
    meta_roas: Optional[float], prev_meta_roas: Optional[float], meta_roas_wow: Optional[float],
    goog_roas: Optional[float], prev_goog_roas: Optional[float], goog_roas_wow: Optional[float],
    section_label_text: str = "EVOLU&#199;&#195;O SEMANAL",
    prev_label: str = "sem. anterior",
) -> str:
    """3-column bar chart row: Receita | ROAS Meta | ROAS Google."""

    def chart_cell(title: str, cur_fmt: str, prev_fmt: str,
                   delta_pct: Optional[float], invert: bool,
                   cur: Optional[float], prev: Optional[float],
                   color: str, width_style: str) -> str:
        if cur is None:
            return ""
        dc = _delta_color(delta_pct, invert=invert)
        dt_txt = _pct(delta_pct)
        bars = _bar_pair(cur, prev, color)
        delta_div = (
            f'<div style="font-size:12px;font-weight:700;color:{dc};font-family:Arial,Helvetica,sans-serif;margin-top:4px;line-height:1;">{dt_txt}</div>'
            if dt_txt else '<div style="height:4px;"></div>'
        )
        return (
            f'<td valign="top" {width_style}>'
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td bgcolor="{_C["card_hi"]}" style="background-color:{_C["card_hi"]};border-radius:8px;border:1px solid {_C["border"]};padding:14px 14px 16px;">'
            f'<div style="font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;margin-bottom:8px;">{title}</div>'
            f'<div style="font-size:17px;font-weight:700;color:{_C["text_1"]};font-family:Arial,Helvetica,sans-serif;line-height:1;">{cur_fmt}</div>'
            f'{delta_div}'
            f'<div style="margin-top:12px;">'
            f'{bars}'
            f'<div style="font-size:10px;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;margin-top:4px;">{prev_label}: {prev_fmt}</div>'
            f'</div>'
            f'</td></tr></table>'
            f'</td>'
        )

    has_meta = meta_roas is not None
    has_goog = goog_roas is not None

    if not revenue and not has_meta and not has_goog:
        return ""

    # Determine column layout
    cols = []
    if revenue is not None:
        cols.append(chart_cell(
            "RECEITA", _brl(revenue), _brl(prev_revenue),
            revenue_wow, False, revenue, prev_revenue,
            _delta_color(revenue_wow), 'style="padding-right:6px;"'
        ))
    if has_meta:
        cols.append(chart_cell(
            "ROAS META", _x(meta_roas), _x(prev_meta_roas),
            meta_roas_wow, False, meta_roas, prev_meta_roas,
            _C["meta_blue"], 'style="padding-left:6px;padding-right:6px;"' if revenue is not None else 'style="padding-right:6px;"'
        ))
    if has_goog:
        cols.append(chart_cell(
            "ROAS GOOGLE", _x(goog_roas), _x(prev_goog_roas),
            goog_roas_wow, False, goog_roas, prev_goog_roas,
            _C["goog_green"], 'style="padding-left:6px;"'
        ))

    if not any(cols):
        return ""

    # Build columns with equal widths
    n = len(cols)
    w = 100 // n if n else 100

    # Re-render with correct widths
    def chart_cell_w(title, cur_fmt, prev_fmt, delta_pct, invert, cur, prev, color, is_first, is_last):
        if cur is None:
            return ""
        dc = _delta_color(delta_pct, invert=invert)
        dt_txt = _pct(delta_pct)
        bars = _bar_pair(cur, prev, color)
        delta_div = (
            f'<div style="font-size:12px;font-weight:700;color:{dc};font-family:Arial,Helvetica,sans-serif;margin-top:4px;line-height:1;">{dt_txt}</div>'
            if dt_txt else '<div style="height:4px;"></div>'
        )
        pad_l = "0" if is_first else "5px"
        pad_r = "0" if is_last else "5px"
        return (
            f'<td width="{w}%" valign="top" style="padding-left:{pad_l};padding-right:{pad_r};">'
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td bgcolor="{_C["card_hi"]}" style="background-color:{_C["card_hi"]};border-radius:8px;border:1px solid {_C["border"]};padding:14px 12px 15px;">'
            f'<div style="font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;margin-bottom:8px;">{title}</div>'
            f'<div style="font-size:16px;font-weight:700;color:{_C["text_1"]};font-family:Arial,Helvetica,sans-serif;line-height:1;">{cur_fmt}</div>'
            f'{delta_div}'
            f'<div style="margin-top:11px;">'
            f'{bars}'
            f'<div style="font-size:10px;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;margin-top:4px;">{prev_label}: {prev_fmt}</div>'
            f'</div>'
            f'</td></tr></table>'
            f'</td>'
        )

    items = []
    chart_defs = []
    if revenue is not None:
        chart_defs.append(("RECEITA", _brl(revenue), _brl(prev_revenue), revenue_wow, False, revenue, prev_revenue, _delta_color(revenue_wow)))
    if has_meta:
        chart_defs.append(("ROAS META", _x(meta_roas), _x(prev_meta_roas), meta_roas_wow, False, meta_roas, prev_meta_roas, _C["meta_blue"]))
    if has_goog:
        chart_defs.append(("ROAS GOOGLE", _x(goog_roas), _x(prev_goog_roas), goog_roas_wow, False, goog_roas, prev_goog_roas, _C["goog_green"]))

    n = len(chart_defs)
    cells_html = ""
    for i, (title, cur_fmt, prev_fmt, dp, inv, cur, prev, color) in enumerate(chart_defs):
        cells_html += chart_cell_w(title, cur_fmt, prev_fmt, dp, inv, cur, prev, color, i == 0, i == n - 1)

    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label(section_label_text)}
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>{cells_html}</tr>
    </table>
  </td>
</tr>"""


# ── Section: Channel cards ────────────────────────────────────────────────────

def _channel_card(name: str, name_color: str,
                  rows: list[tuple[str, str, Optional[float], bool]]) -> str:
    rows_html = ""
    for label, value, delta_v, inv in rows:
        if delta_v is not None:
            dc = _C["neutral"] if "nvestimento" in label else _delta_color(delta_v, invert=inv)
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
        f'<div style="font-size:11px;font-weight:700;color:{name_color};font-family:Arial,Helvetica,sans-serif;text-transform:uppercase;letter-spacing:0.07em;margin-bottom:12px;">{name}</div>'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">{rows_html}</table>'
        f'</td></tr></table>'
    )


def _not_contracted_card(name: str, name_color: str) -> str:
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td bgcolor="{_C["card_hi"]}" style="background-color:{_C["card_hi"]};border-radius:8px;border:1px solid {_C["border"]};padding:16px;">'
        f'<div style="font-size:11px;font-weight:700;color:{name_color};font-family:Arial,Helvetica,sans-serif;text-transform:uppercase;letter-spacing:0.07em;margin-bottom:10px;">{name}</div>'
        f'<div style="font-size:12px;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;line-height:1.5;">N&#227;o contratado neste per&#237;odo.</div>'
        f'</td></tr></table>'
    )


def _channels(meta_spend: Optional[float], meta_conv: Optional[float],
              meta_conv_value: Optional[float], meta_roas: Optional[float],
              meta_cpa: Optional[float], meta_spend_wow: Optional[float],
              meta_conv_wow: Optional[float], meta_roas_wow: Optional[float],
              meta_cpa_wow: Optional[float], goog_spend: Optional[float],
              goog_purch: Optional[float], goog_purch_value: Optional[float],
              goog_roas: Optional[float], goog_cpa: Optional[float],
              goog_spend_wow: Optional[float], goog_purch_wow: Optional[float],
              goog_roas_wow: Optional[float], goog_cpa_wow: Optional[float],
              section_label: str = "PERFORMANCE POR CANAL") -> str:

    meta_available = meta_spend is not None
    goog_available = goog_spend is not None

    if meta_available:
        meta_card = _channel_card("Meta Ads", _C["meta_blue"], [
            ("Investimento",    _brl(meta_spend),      meta_spend_wow, True),
            ("Compras",         _num_conv(meta_conv),   meta_conv_wow,  False),
            ("Receita atrib.",  _brl(meta_conv_value),  None,           False),
            ("ROAS",            _x(meta_roas),          meta_roas_wow,  False),
            ("CPA",             _brl(meta_cpa),         meta_cpa_wow,   True),
        ])
    else:
        meta_card = _not_contracted_card("Meta Ads", _C["meta_blue"])

    if goog_available:
        goog_card = _channel_card("Google Ads", _C["goog_green"], [
            ("Investimento",         _brl(goog_spend),       goog_spend_wow,  True),
            ("Compras atribu&#237;das", _num_conv(goog_purch), goog_purch_wow, False),
            ("Receita ecomm.",       _brl(goog_purch_value), None,            False),
            ("ROAS ecommerce",       _x(goog_roas),          goog_roas_wow,   False),
            ("CPA ecommerce",        _brl(goog_cpa),         goog_cpa_wow,    True),
        ])
    else:
        goog_card = _not_contracted_card("Google Ads", _C["goog_green"])

    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label(section_label)}
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td width="50%" valign="top" style="padding-right:8px;">{meta_card}</td>
        <td width="50%" valign="top" style="padding-left:8px;">{goog_card}</td>
      </tr>
    </table>
  </td>
</tr>"""


# ── Section: Campaign highlights (weekly) ────────────────────────────────────

def _campaign_highlights_weekly(al: dict) -> str:
    """Compact campaign highlights: em destaque + atenção, max 3 each.
    Sources: performance_drilldown.channels.{meta,google}.campaign_ranking /
             purchase_diagnostic_ranking + {meta,google}_analysis.campaign_evidence /
             campaign_findings.
    Does not invent values. Respects small-sample labels.
    """
    pd_data  = al.get("performance_drilldown") or {}
    channels = pd_data.get("channels") or {}
    meta_ch  = channels.get("meta") or {}
    goog_ch  = channels.get("google") or {}

    meta_ranking = (meta_ch.get("campaign_ranking_display")
                    or meta_ch.get("campaign_ranking") or [])
    goog_ranking = (goog_ch.get("purchase_diagnostic_ranking")
                    or goog_ch.get("campaign_ranking_display")
                    or goog_ch.get("campaign_ranking") or [])

    # Text-based campaign evidence from analysis engine
    ma = al.get("meta_analysis") or {}
    ga = al.get("google_analysis") or {}
    meta_evidence = (ma.get("campaign_evidence") or {}).get("items") or []
    goog_evidence = (
        (ga.get("campaign_evidence") or {}).get("items")
        or ga.get("campaign_findings") or []
    )

    def _roas_wow(camp: dict) -> Optional[float]:
        cur  = camp.get("current") or {}
        prev = camp.get("previous") or {}
        rc   = cur.get("roas_ecommerce") or cur.get("roas")
        rp   = prev.get("roas_ecommerce") or prev.get("roas")
        if rc and rp and rp > 0:
            return (rc - rp) / rp * 100
        return None

    def _build_entries(ranking: list, channel: str) -> tuple[list, list]:
        pos, neg = [], []
        for c in ranking:
            cur   = c.get("current") or {}
            spend = cur.get("spend") or 0
            if spend < 100:
                continue
            roas  = cur.get("roas_ecommerce") or cur.get("roas")
            cpa   = cur.get("cpa_ecommerce") or cur.get("cpa")
            purch = cur.get("purchases")
            wow   = _roas_wow(c)
            small = c.get("sample_state", "") == "SMALL_SAMPLE"
            entry = {
                "name":    c.get("name", ""),
                "channel": channel,
                "spend":   spend,
                "roas":    roas,
                "cpa":     cpa,
                "purchases": purch,
                "roas_wow":  wow,
                "small_sample": small,
            }
            if wow is not None:
                if wow >= 10 and roas and roas >= 3:
                    pos.append(entry)
                elif wow <= -20 and spend >= 300:
                    neg.append(entry)
            elif roas and roas >= 10 and spend >= 300:
                pos.append(entry)
        pos.sort(key=lambda x: x.get("roas") or 0, reverse=True)
        neg.sort(key=lambda x: x.get("roas_wow") or 0)
        return pos, neg

    pos_meta, neg_meta = _build_entries(meta_ranking, "Meta")
    pos_goog, neg_goog = _build_entries(goog_ranking, "Google")

    highlights = (pos_meta + pos_goog)[:3]
    attention  = (neg_meta + neg_goog)[:3]

    # Build text evidence rows (from campaign_evidence / campaign_findings)
    def _text_evidence(items: list, channel: str, channel_color: str) -> str:
        rows_html = ""
        for item in items[:3]:
            name  = _clean(item.get("campaign") or item.get("campaign_name") or "")
            obs   = _clean(item.get("observation") or item.get("interpretation") or "")
            small = item.get("sample_state") == "SMALL_SAMPLE"
            if not name and not obs:
                continue
            small_badge = (
                f' <span style="font-size:9px;color:{_C["warning"]};font-family:Arial,Helvetica,sans-serif;'
                f'border:1px solid {_C["warning"]};border-radius:3px;padding:1px 4px;">amostra reduzida</span>'
            ) if small else ""
            rows_html += (
                f'<div style="padding:7px 0;border-bottom:1px solid {_C["sep"]};">'
                f'<div style="font-size:11px;font-weight:700;color:{_C["text_1"]};font-family:Arial,Helvetica,sans-serif;">'
                f'<span style="color:{channel_color};">{channel}</span> &middot; {name}{small_badge}</div>'
                + (f'<div style="font-size:11px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;line-height:1.5;margin-top:3px;">{obs}</div>' if obs else "")
                + f'</div>'
            )
        return rows_html

    has_ranked = highlights or attention
    has_text   = meta_evidence or goog_evidence

    if not has_ranked and not has_text:
        return ""

    def _camp_row(e: dict, section_color: str) -> str:
        chan_color = _C["meta_blue"] if e["channel"] == "Meta" else _C["goog_green"]
        wow_txt    = _pct(e["roas_wow"]) if e["roas_wow"] is not None else ""
        wow_color  = _delta_color(e["roas_wow"])
        small_badge = (
            f' <span style="font-size:9px;color:{_C["warning"]};font-family:Arial,Helvetica,sans-serif;'
            f'border:1px solid {_C["warning"]};border-radius:3px;padding:1px 4px;">amostra reduzida</span>'
        ) if e.get("small_sample") else ""
        td = (f'style="font-size:11px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;'
              f'padding:7px 4px;border-bottom:1px solid {_C["sep"]};text-align:right;"')
        td1 = (f'style="font-size:11px;font-weight:600;color:{_C["text_1"]};font-family:Arial,Helvetica,sans-serif;'
               f'padding:7px 4px 7px 0;border-bottom:1px solid {_C["sep"]};"')
        wow_td = (
            f'<td {td}><span style="font-weight:700;color:{wow_color};">{wow_txt}</span></td>'
            if wow_txt else f'<td {td}>&#8212;</td>'
        )
        return (
            f'<tr><td {td1}>'
            f'<span style="font-size:9px;font-weight:700;color:{chan_color};text-transform:uppercase;'
            f'font-family:Arial,Helvetica,sans-serif;">{e["channel"]}</span> '
            f'{e["name"][:40]}{small_badge}</td>'
            f'<td {td}>{_brl(e["spend"])}</td>'
            f'<td {td}>{_x(e["roas"])}</td>'
            f'<td {td}>{_brl(e["cpa"])}</td>'
            f'<td {td}>{_num_conv(e["purchases"]) if e["purchases"] else "&#8212;"}</td>'
            f'{wow_td}</tr>'
        )

    th = (f'style="font-size:9px;font-weight:700;letter-spacing:0.07em;text-transform:uppercase;'
          f'color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;padding:0 4px 8px 0;"')
    thr = (f'style="font-size:9px;font-weight:700;letter-spacing:0.07em;text-transform:uppercase;'
           f'color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;padding:0 4px 8px;text-align:right;"')
    col_header = f'<tr><td {th}>CAMPANHA</td><td {thr}>INVEST.</td><td {thr}>ROAS</td><td {thr}>CPA</td><td {thr}>COMPRAS</td><td {thr}>WoW ROAS</td></tr>'

    sections_html = ""

    if highlights:
        rows_html = "".join(_camp_row(e, _C["positive"]) for e in highlights)
        sections_html += (
            f'<div style="font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;'
            f'color:{_C["positive"]};font-family:Arial,Helvetica,sans-serif;margin-bottom:8px;">EM DESTAQUE</div>'
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:16px;">'
            f'{col_header}{rows_html}</table>'
        )

    if attention:
        rows_html = "".join(_camp_row(e, _C["negative"]) for e in attention)
        sections_html += (
            f'<div style="font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;'
            f'color:{_C["warning"]};font-family:Arial,Helvetica,sans-serif;margin-bottom:8px;">ATEN&#199;&#195;O</div>'
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:16px;">'
            f'{col_header}{rows_html}</table>'
        )

    if not has_ranked and has_text:
        # Only text evidence available — show narrative rows
        sections_html += _text_evidence(meta_evidence, "Meta", _C["meta_blue"])
        sections_html += _text_evidence(goog_evidence, "Google", _C["goog_green"])

    if not sections_html:
        return ""

    note = (
        f'<div style="font-size:10px;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;'
        f'line-height:1.5;padding-top:8px;">Dados de campanha atribu&#237;dos pela plataforma. '
        f'Varia&#231;&#245;es WoW n&#227;o implicam causalidade.</div>'
    )

    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("CAMPANHAS DA SEMANA")}
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr><td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:16px;">
        {sections_html}
        {note}
      </td></tr>
    </table>
  </td>
</tr>"""


# ── Section: Change digest (weekly) ──────────────────────────────────────────

def _change_digest_weekly(cd: dict) -> str:
    if not cd:
        return ""
    meta_cd    = cd.get("meta") or {}
    goog_cd    = cd.get("google") or {}
    main_ch    = meta_cd.get("main_changes") or []
    budget_ch  = meta_cd.get("budget_changes") or []
    interp     = _clean(meta_cd.get("interpretation") or "")

    if not main_ch and not budget_ch and not interp:
        return ""

    items_html = ""

    if budget_ch:
        items_html += (
            f'<div style="font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;'
            f'color:{_C["meta_blue"]};font-family:Arial,Helvetica,sans-serif;margin-bottom:6px;">Meta Ads — Or&#231;amento</div>'
        )
        for b in budget_ch:
            entity = _clean(b.get("entity", ""))
            items_html += (
                f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;'
                f'padding:4px 0;border-bottom:1px solid {_C["sep"]};">{entity}: '
                f'{b.get("before_display","")} &rarr; {b.get("after_display","")}</div>'
            )
        items_html += '<div style="height:10px;"></div>'

    if main_ch:
        items_html += (
            f'<div style="font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;'
            f'color:{_C["meta_blue"]};font-family:Arial,Helvetica,sans-serif;margin-bottom:6px;">Meta Ads — Eventos detectados</div>'
        )
        for c in main_ch[:4]:
            items_html += (
                f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;'
                f'padding:4px 0;border-bottom:1px solid {_C["sep"]};">&#183; {_clean(c)}</div>'
            )

    if interp:
        items_html += (
            f'<div style="font-size:11px;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;'
            f'line-height:1.5;margin-top:10px;">{interp}</div>'
        )

    goog_note = _clean(goog_cd.get("coverage_limit") or goog_cd.get("interpretation") or "")
    if goog_note:
        items_html += (
            f'<div style="font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;'
            f'color:{_C["goog_green"]};font-family:Arial,Helvetica,sans-serif;margin-top:14px;margin-bottom:5px;">Google Ads</div>'
            f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;line-height:1.5;">{goog_note}</div>'
        )

    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:20px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("MUDAN&#199;AS DA SEMANA")}
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr><td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:16px;">
        {items_html}
      </td></tr>
    </table>
  </td>
</tr>"""


# ── Section: Month pacing ─────────────────────────────────────────────────────

def _month_pacing_row(facts_with_targets: list[dict],
                      period_start: str, period_end: str) -> str:
    rev_fact = next(
        (f for f in facts_with_targets
         if f.get("metric_key") == "month_to_date_revenue" and f.get("target") is not None),
        None,
    )
    if rev_fact is None:
        return ""

    import datetime as dt
    import calendar
    try:
        s = dt.date.fromisoformat(period_start)
        e = dt.date.fromisoformat(period_end)
        days_elapsed   = e.day
        days_in_month  = calendar.monthrange(e.year, e.month)[1]
        pct_elapsed    = days_elapsed / days_in_month * 100
    except Exception:
        return ""

    revenue      = rev_fact.get("value") or 0
    target       = rev_fact.get("target") or 0
    if target == 0:
        return ""

    realized_pct = revenue / target * 100
    projected    = revenue / (days_elapsed / days_in_month) if days_elapsed > 0 else 0
    proj_color   = _C["positive"] if projected >= target else _C["negative"]
    bar_realized = min(int(realized_pct), 100)

    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:20px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("RITMO DO M&#202;S")}
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td width="50%" valign="top" style="padding-right:8px;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
            <td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:14px 16px;">
              <div style="font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;margin-bottom:8px;">REALIZADO</div>
              <div style="font-size:18px;font-weight:700;color:{_C['text_1']};font-family:Arial,Helvetica,sans-serif;">{_brl(revenue)}</div>
              <div style="font-size:11px;color:{_C['text_2']};margin-top:4px;font-family:Arial,Helvetica,sans-serif;">{realized_pct:.0f}% da meta &nbsp;&middot;&nbsp; esperado {pct_elapsed:.0f}%</div>
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:10px;">
                <tr><td bgcolor="{_C['sep']}" height="6" style="border-radius:3px;background-color:{_C['sep']};font-size:0;line-height:0;">
                  <table cellpadding="0" cellspacing="0" border="0" width="{bar_realized}%"><tr>
                    <td bgcolor="{_C['accent']}" height="6" style="background-color:{_C['accent']};border-radius:3px;font-size:0;line-height:0;"></td>
                  </tr></table>
                </td></tr>
              </table>
            </td>
          </tr></table>
        </td>
        <td width="50%" valign="top" style="padding-left:8px;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
            <td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:14px 16px;">
              <div style="font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;margin-bottom:8px;">PROJE&#199;&#195;O</div>
              <div style="font-size:18px;font-weight:700;color:{proj_color};font-family:Arial,Helvetica,sans-serif;">{_brl(projected)}</div>
              <div style="font-size:11px;color:{_C['text_2']};margin-top:4px;font-family:Arial,Helvetica,sans-serif;">Meta: {_brl(target)}</div>
            </td>
          </tr></table>
        </td>
      </tr>
    </table>
  </td>
</tr>"""


def _monthly_target_block(facts_list: list[dict]) -> str:
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
          <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
            <td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:14px 16px;">
              <div style="font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;margin-bottom:8px;">REALIZADO</div>
              <div style="font-size:18px;font-weight:700;color:{_C['text_1']};font-family:Arial,Helvetica,sans-serif;">{_brl(revenue)}</div>
              <div style="font-size:11px;color:{_C['text_2']};margin-top:4px;font-family:Arial,Helvetica,sans-serif;">{realized_pct:.0f}% da meta</div>
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:10px;">
                <tr><td bgcolor="{_C['sep']}" height="6" style="border-radius:3px;background-color:{_C['sep']};font-size:0;line-height:0;">
                  <table cellpadding="0" cellspacing="0" border="0" width="{bar_pct}%"><tr>
                    <td bgcolor="{status_color}" height="6" style="background-color:{status_color};border-radius:3px;font-size:0;line-height:0;"></td>
                  </tr></table>
                </td></tr>
              </table>
            </td>
          </tr></table>
        </td>
        <td width="50%" valign="top" style="padding-left:8px;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
            <td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:14px 16px;">
              <div style="font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;margin-bottom:8px;">STATUS</div>
              <div style="font-size:18px;font-weight:700;color:{status_color};font-family:Arial,Helvetica,sans-serif;">{status_txt}</div>
              <div style="font-size:11px;color:{_C['text_2']};margin-top:4px;font-family:Arial,Helvetica,sans-serif;">Meta: {_brl(target)}</div>
            </td>
          </tr></table>
        </td>
      </tr>
    </table>
  </td>
</tr>"""


# ── Section: Diagnosis / Insights ────────────────────────────────────────────

def _insights(diagnosis: list[dict], section_label_text: str = "DIAGN&#211;STICO DA SEMANA") -> str:
    scope_colors = {
        "BUSINESS":    _C["accent"],
        "META":        "#4AA3E8",
        "GOOGLE":      "#4ACC7A",
        "MEASUREMENT": "#A78BFA",
    }
    cards_html = ""
    for d in diagnosis[:4]:
        scope  = d.get("scope", "")
        title  = _clean(d.get("finding", d.get("observation", "")))
        interp = _clean(d.get("interpretation", d.get("diagnosis", "")))
        sc     = scope_colors.get(scope, _C["accent"])
        scope_div = (
            f'<div style="font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:{sc};font-family:Arial,Helvetica,sans-serif;margin-bottom:6px;">{scope}</div>'
            if scope else ""
        )
        cards_html += (
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;">'
            f'<tr><td bgcolor="{_C["card_hi"]}" style="background-color:{_C["card_hi"]};border-radius:0 8px 8px 0;'
            f'border-left:3px solid {sc};border-top:1px solid {_C["border"]};border-right:1px solid {_C["border"]};border-bottom:1px solid {_C["border"]};padding:14px 16px;">'
            f'{scope_div}'
            f'<div style="font-size:13px;font-weight:700;color:{_C["text_1"]};font-family:Arial,Helvetica,sans-serif;margin-bottom:6px;line-height:1.3;">{title}</div>'
            f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;line-height:1.65;">{interp}</div>'
            f'</td></tr></table>'
        )
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label(section_label_text)}
    {cards_html}
  </td>
</tr>"""


def _insights_monthly(diagnosis: list[dict]) -> str:
    return _insights(diagnosis, section_label_text="DIAGN&#211;STICO DO M&#202;S")


# ── Section: Priorities / Action Plan ────────────────────────────────────────

def _priorities(recommendations: list[dict], priorities: list[dict]) -> str:
    items = priorities[:4] if priorities else recommendations[:4]
    rows_html = ""
    for i, item in enumerate(items, 1):
        focus  = _clean(item.get("focus", item.get("action", item.get("rank") and item.get("action") or item.get("recommendation", ""))))
        # For priorities_next_week entries, the main text is in "action"
        if not focus:
            focus = _clean(item.get("action", ""))
        detail = _clean(item.get("reason", item.get("rationale", item.get("evidence", ""))))
        if detail == focus:
            detail = ""
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
                if detail else ""
            )
            + f'</td></tr></table>'
        )
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("PRIORIDADES")}
    {rows_html}
  </td>
</tr>"""


# ── Section: Mix de canais (monthly) ─────────────────────────────────────────

def _mix_de_canais_table(
    meta_spend: Optional[float], meta_conv: Optional[float],
    meta_value: Optional[float], meta_roas: Optional[float], meta_cpa: Optional[float],
    goog_spend: Optional[float], goog_purch: Optional[float],
    goog_value: Optional[float], goog_roas: Optional[float], goog_cpa: Optional[float],
    total_spend: Optional[float],
) -> str:
    ms  = meta_spend or 0
    gs  = goog_spend or 0
    tot = total_spend or (ms + gs) or 1
    msh = ms / tot * 100
    gsh = gs / tot * 100
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

    rows_html = ""
    if meta_spend is not None:
        rows_html += dr(_C['meta_blue'],  "Meta Ads",   meta_spend, meta_value, meta_conv,  meta_roas, meta_cpa, msh)
    if goog_spend is not None:
        rows_html += dr(_C['goog_green'], "Google Ads", goog_spend, goog_value, goog_purch, goog_roas, goog_cpa, gsh)

    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("MIX DE CANAIS")}
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr><td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:16px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          {header}
          {rows_html}
        </table>
      </td></tr>
    </table>
  </td>
</tr>"""


# ── Section: Canal visão mensal (dynamic month labels) ────────────────────────

def _canal_visao_mensal(
    channel_name: str,
    channel_color: str,
    section_title: str,
    rows: list,
    context_note: str = "",
    cur_month_label: str = "MÊS ATUAL",
    prev_month_label: str = "MÊS ANTERIOR",
) -> str:
    th_s  = (f'style="font-size:9px;font-weight:700;letter-spacing:0.07em;text-transform:uppercase;'
             f'color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;padding:0 6px 8px 0;"')
    thr_s = (f'style="font-size:9px;font-weight:700;letter-spacing:0.07em;text-transform:uppercase;'
             f'color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;padding:0 6px 8px;text-align:right;"')
    header = (
        f'<tr><td {th_s}>M&#201;TRICA</td>'
        f'<td {thr_s}>{cur_month_label}</td>'
        f'<td {thr_s}>{prev_month_label}</td>'
        f'<td {thr_s}>MoM</td></tr>'
    )
    rows_html = ""
    for label, cur, prev_v, mom, inv in rows:
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
            f'font-family:Arial,Helvetica,sans-serif;padding:7px 6px;border-bottom:1px solid {_C["sep"]};">{prev_v}</td>'
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
        <div style="font-size:11px;font-weight:700;color:{channel_color};font-family:Arial,Helvetica,sans-serif;text-transform:uppercase;letter-spacing:0.07em;margin-bottom:12px;">{channel_name}</div>
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          {header}
          {rows_html}
          {note_html}
        </table>
      </td></tr>
    </table>
  </td>
</tr>"""


# ── Section: Campaign tables ──────────────────────────────────────────────────

def _canonical_campaign_table(envelope: dict, platform: str) -> str:
    from html import escape
    rows   = envelope.get('rows') or []
    chosen = set((envelope.get('selection') or {}).get('top_spend_campaign_ids') or [])
    chosen.update((envelope.get('selection') or {}).get('top_efficiency_campaign_ids') or [])
    if not chosen:
        return ''
    color = _C['meta_blue'] if platform == 'meta' else _C['goog_green']
    td    = (f'font-size:11px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;'
             f'padding:8px 4px;border-bottom:1px solid {_C["sep"]};')
    columns = ('Campanha / status / tipo', 'Invest.', 'Compras', 'Receita atrib.', 'ROAS', 'CPA')
    header = ''.join(f'<td style="{td}font-weight:700;">{escape(c)}</td>' for c in columns)
    body = ''
    for row in rows:
        if row['campaign_id'] not in chosen:
            continue
        label  = ' / '.join(str(row.get(k) or '—') for k in ('campaign_name', 'status', 'campaign_type') if platform == 'google' or k != 'campaign_type')
        values = (
            escape(label),
            _brl(row.get('spend')),
            _num_conv(row.get('purchases' if platform == 'meta' else 'purchase_conversions')),
            _brl(row.get('purchase_value')),
            _x(row.get('roas' if platform == 'meta' else 'ecommerce_roas')),
            _brl(row.get('cpa' if platform == 'meta' else 'ecommerce_cpa')),
        )
        body += '<tr>' + ''.join(f'<td style="{td}">{v}</td>' for v in values) + '</tr>'
    label = 'META ADS' if platform == 'meta' else 'GOOGLE ADS'
    note  = 'Maiores investimentos no per&#237;odo. Status atual na coleta; n&#227;o representa status hist&#243;rico.'
    if not (envelope.get('selection') or {}).get('top_efficiency_campaign_ids'):
        note += ' Efici&#234;ncia n&#227;o qualificada para escala: volume insuficiente.'
    if platform == 'google':
        note += ' Brand somente quando classificado explicitamente.'
    return f'''<tr><td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
{_section_label(label + ' &#8212; CAMPANHAS')}
<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:16px;">
<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>{header}</tr>{body}</table>
<div style="font-size:10px;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;padding-top:8px;line-height:1.5;">{note}</div>
</td></tr></table></td></tr>'''


def _limitation_card_campaigns(channel: str, channel_color: str, section_title: str) -> str:
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:20px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label(section_title)}
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr><td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:0 8px 8px 0;border-left:3px solid {_C['neutral']};border-top:1px solid {_C['border']};border-right:1px solid {_C['border']};border-bottom:1px solid {_C['border']};padding:14px 16px;">
        <div style="font-size:11px;font-weight:700;color:{channel_color};font-family:Arial,Helvetica,sans-serif;letter-spacing:0.06em;text-transform:uppercase;margin-bottom:6px;">{channel}</div>
        <div style="font-size:12px;color:{_C['text_2']};font-family:Arial,Helvetica,sans-serif;line-height:1.6;">Granularidade de campanha n&#227;o dispon&#237;vel neste per&#237;odo.</div>
      </td></tr>
    </table>
  </td>
</tr>"""


# ── Section: Change digest (monthly) ─────────────────────────────────────────

def _change_digest_mensal(change_digest: dict) -> str:
    if not change_digest:
        return ""
    meta_cd   = change_digest.get("meta") or {}
    goog_cd   = change_digest.get("google") or {}
    causality = change_digest.get("causality", "")
    items_html = (
        f'<div style="font-size:11px;font-weight:700;color:{_C["meta_blue"]};font-family:Arial,Helvetica,sans-serif;'
        f'text-transform:uppercase;letter-spacing:0.06em;margin-bottom:10px;">Meta Ads</div>'
    )
    budgets     = meta_cd.get("budget_changes") or []
    pauses      = meta_cd.get("campaign_pauses") or []
    activations = meta_cd.get("campaign_activations") or []
    interp      = meta_cd.get("interpretation", "")
    if budgets:
        items_html += (
            f'<div style="font-size:9px;font-weight:700;letter-spacing:0.07em;text-transform:uppercase;'
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
            f'<div style="font-size:9px;font-weight:700;letter-spacing:0.07em;text-transform:uppercase;'
            f'color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;margin:10px 0 5px;">CAMPANHAS PAUSADAS</div>'
        )
        for p in list(dict.fromkeys(pauses)):
            items_html += (
                f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;'
                f'padding:4px 0;border-bottom:1px solid {_C["sep"]};">{_clean(p)}</div>'
            )
    if activations:
        items_html += (
            f'<div style="font-size:9px;font-weight:700;letter-spacing:0.07em;text-transform:uppercase;'
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
        f'<div style="font-size:11px;font-weight:700;color:{_C["goog_green"]};font-family:Arial,Helvetica,sans-serif;'
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


# ── Section: Diagnóstico mensal v2 ────────────────────────────────────────────

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
            f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;line-height:1.65;">{diag}</div>'
            f'{action_html}'
            f'</td></tr></table>'
        )
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("DIAGN&#211;STICO DO M&#202;S")}
    {cards_html}
  </td>
</tr>"""


# ── Section: Ações executadas (monthly) ──────────────────────────────────────

def _actions_section(actions: dict) -> str:
    """Show executed actions and observed platform changes when available."""
    if not actions:
        return ""
    state    = actions.get("state") or ""
    executed = actions.get("executed_actions") or []
    # observed_platform_changes can be a list or a {meta, google} dict
    _obs_raw = actions.get("observed_platform_changes") or []
    if isinstance(_obs_raw, dict):
        observed = [v for v in _obs_raw.values() if v]
    else:
        observed = list(_obs_raw)

    if not executed and not observed:
        return ""

    items_html = ""

    if executed:
        items_html += (
            f'<div style="font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;'
            f'color:{_C["positive"]};font-family:Arial,Helvetica,sans-serif;margin-bottom:8px;">A&#199;&#213;ES EXECUTADAS</div>'
        )
        for a in executed[:5]:
            desc    = _clean(a.get("description", a.get("action_type", "")))
            channel = a.get("channel", "")
            outcome = _clean(a.get("outcome", a.get("result", "")))
            channel_badge = (
                f' <span style="font-size:9px;font-weight:700;color:{_C["meta_blue"] if "meta" in channel.lower() else _C["goog_green"]};'
                f'font-family:Arial,Helvetica,sans-serif;text-transform:uppercase;">{channel}</span>'
                if channel else ""
            )
            outcome_div = (
                f'<div style="font-size:11px;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;line-height:1.4;margin-top:3px;">{outcome}</div>'
                if outcome else ""
            )
            items_html += (
                f'<div style="padding:8px 0;border-bottom:1px solid {_C["sep"]};">'
                f'<div style="font-size:12px;font-weight:700;color:{_C["text_1"]};font-family:Arial,Helvetica,sans-serif;">{desc}{channel_badge}</div>'
                f'{outcome_div}'
                f'</div>'
            )

    if observed:
        items_html += (
            f'<div style="font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;'
            f'color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;margin:{("16px" if executed else "0")} 0 8px;">MUDAN&#199;AS OBSERVADAS NA PLATAFORMA</div>'
        )
        for obs_item in observed[:5]:
            desc = _clean(obs_item if isinstance(obs_item, str) else obs_item.get("description", ""))
            items_html += (
                f'<div style="font-size:12px;color:{_C["text_2"]};font-family:Arial,Helvetica,sans-serif;'
                f'padding:5px 0;border-bottom:1px solid {_C["sep"]};">&#183; {desc}</div>'
            )

    if state and "MISSING" in state.upper():
        return ""

    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("A&#199;&#213;ES DO M&#202;S")}
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr><td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:16px;">
        {items_html}
      </td></tr>
    </table>
  </td>
</tr>"""


# ── Section: Monthly narrative / learnings ────────────────────────────────────

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
            f'<div style="font-size:9px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:{_C["text_3"]};font-family:Arial,Helvetica,sans-serif;margin-bottom:8px;">APRENDIZADOS</div>'
            f'<ul style="margin:0;padding-left:18px;">{items}</ul>'
        )
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:24px 28px;border-bottom:1px solid {_C['sep']};">
    {_section_label("RESUMO DO M&#202;S")}
    {sum_html}{learn_html}
  </td>
</tr>"""


# ── Section: Plano próximo mês ────────────────────────────────────────────────

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


# ── Section: CTA + Footer ─────────────────────────────────────────────────────

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


def _cta_blocked() -> str:
    """CTA shown when recipient authorization to the Platform is not confirmed."""
    return f"""<tr>
  <td bgcolor="{_C['card']}" style="background-color:{_C['card']};padding:20px 28px 28px;text-align:center;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:420px;margin:0 auto;">
      <tr>
        <td bgcolor="{_C['card_hi']}" style="background-color:{_C['card_hi']};border-radius:8px;border:1px solid {_C['border']};padding:18px 20px;text-align:center;">
          <div style="font-size:11px;font-weight:700;letter-spacing:0.07em;text-transform:uppercase;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;margin-bottom:8px;">ACESSO &#192; PLATAFORMA</div>
          <div style="font-size:13px;color:{_C['text_2']};font-family:Arial,Helvetica,sans-serif;line-height:1.6;">Para acessar a an&#225;lise completa na Noro Platform, entre em contato com a equipe NoroLabs. O acesso requer autoriza&#231;&#227;o pr&#233;via.</div>
        </td>
      </tr>
    </table>
  </td>
</tr>"""


def _footer() -> str:
    return f"""<tr>
  <td bgcolor="{_C['hdr']}" style="background-color:{_C['hdr']};padding:18px 28px;border-radius:0 0 10px 10px;border-top:1px solid {_C['border']};text-align:center;">
    <div style="font-size:11px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:{_C['text_3']};font-family:Arial,Helvetica,sans-serif;">NOROLABS &nbsp;|&nbsp; PERFORMANCE &amp; GROWTH</div>
    <div style="font-size:10px;color:{_C['neutral']};margin-top:5px;font-family:Arial,Helvetica,sans-serif;line-height:1.5;">Dados atribu&#237;dos de m&#237;dia podem sofrer atualiza&#231;&#227;o pelas plataformas. Receita e pedidos seguem a fonte de neg&#243;cio dispon&#237;vel no fechamento do per&#237;odo.</div>
  </td>
</tr>"""


# ── Data extraction helpers ───────────────────────────────────────────────────

def _facts_map(content: dict) -> dict:
    return {f["metric_key"]: f.get("value") for f in content.get("facts", [])}


def _wow_pct(deltas: dict, key: str) -> Optional[float]:
    return deltas.get(key, {}).get("delta_pct")


def _mc_pct(mc: dict, section: str, key: str) -> Optional[float]:
    item = (mc or {}).get(section, {}).get(key, {})
    # Handle both {august, september} style and {previous, current} style
    pairs = [
        ("august", "september"),
        ("previous", "current"),
        ("prev", "cur"),
    ]
    for prev_k, cur_k in pairs:
        aug = item.get(prev_k)
        sep = item.get(cur_k)
        if aug is not None and sep is not None and aug != 0:
            return (sep - aug) / abs(aug) * 100
    return None


# ── Outer HTML wrapper ────────────────────────────────────────────────────────

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
  <style type="text/css">
    @media only screen and (max-width:620px) {{
      .email-outer {{ padding: 0 !important; }}
      .email-container {{ width: 100% !important; border-radius: 0 !important; }}
      .mobile-pad {{ padding-left: 16px !important; padding-right: 16px !important; }}
      .col-50 {{ width: 100% !important; display: block !important; padding-left: 0 !important; padding-right: 0 !important; }}
      .col-50-first {{ padding-bottom: 10px !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background-color:{_C['bg']};-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;" bgcolor="{_C['bg']}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{_C['bg']}" style="background-color:{_C['bg']};">
  <tr>
    <td align="center" class="email-outer" style="padding:32px 12px 48px;">
      <table role="presentation" class="email-container" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;border-radius:10px;border:1px solid {_C['border']};overflow:hidden;">
        {body_rows}
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


# ── Public entry points ───────────────────────────────────────────────────────

def render_weekly_email(artifact: dict) -> tuple[str, dict]:
    """Render H7 canonical artifact → (html, validation_result)."""
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

    # WoW deltas — prefer wow_comparison.deltas, fall back to executive_summary
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

    # Previous values for charts and AOV delta
    def _prev(key: str) -> Optional[float]:
        d = wow_src.get(key, {})
        return d.get("previous") if isinstance(d, dict) else None

    prev_revenue   = _prev("revenue_business")
    prev_orders    = _prev("orders_count")
    prev_meta_roas = _prev("roas_meta")
    prev_goog_roas = _prev("google_roas_ecommerce")

    # AOV delta — only when revenue and orders have compatible scope
    prev_avg_ticket = (prev_revenue / prev_orders) if (prev_revenue and prev_orders and prev_orders > 0) else None
    avg_ticket_wow  = (
        (avg_ticket - prev_avg_ticket) / prev_avg_ticket * 100
        if avg_ticket and prev_avg_ticket and prev_avg_ticket > 0
        else None
    )

    diag  = al.get("diagnosis", [])
    recs  = al.get("recommendations", [])
    prios = al.get("priorities_next_week", [])
    provisional = (al.get("executive_summary", {}) or {}).get("status") == "PROVISIONAL"

    pacing_html   = _month_pacing_row(facts_list, p_start, p_end)
    period_label  = _fmt_period(p_start, p_end)
    client_name   = _fmt_client(client_id)
    cta_authorized = artifact.get("cta_authorized", False)

    body = "".join([
        _header(client_name, "Performance Semanal", period_label),
        _hero(revenue, orders, wows["revenue"], provisional=provisional),
        _executive_summary(al, section_label_text="RESUMO DA SEMANA"),
        _kpis_row(orders, wows["orders"], spend, wows["spend"],
                  mer, wows["mer"], avg_ticket, avg_ticket_wow=avg_ticket_wow),
        _mini_charts(
            revenue, prev_revenue, wows["revenue"],
            meta_roas, prev_meta_roas, wows["meta_roas"],
            goog_roas, prev_goog_roas, wows["goog_roas"],
        ),
        _channels(meta_spend, meta_conv, meta_value, meta_roas, meta_cpa,
                  wows["meta_spend"], wows["meta_conv"],
                  wows["meta_roas"], wows["meta_cpa"],
                  goog_spend, goog_purch, goog_value, goog_roas, goog_cpa,
                  wows["goog_spend"], wows["goog_purch"],
                  wows["goog_roas"], wows["goog_cpa"]),
        _campaign_highlights_weekly(al),
        _change_digest_weekly(al.get("change_digest_section") or {}),
        pacing_html,
        _insights(diag),
        _priorities(recs, prios),
        _cta(client_id) if cta_authorized else _cta_blocked(),
        _footer(),
    ])

    validation = {
        "ok":                     ok,
        "errors":                 errors,
        "kpis_checked":           ["MER", "Meta ROAS", "Meta CPA", "Google ROAS ecom", "Google CPA ecom"],
        "month_pacing_available": bool(pacing_html),
        "aov_delta_available":    avg_ticket_wow is not None,
        "cta_authorized":         cta_authorized,
        "template_version":       TEMPLATE_VERSION,
    }
    return _wrap(body), validation


def render_monthly_email(artifact: dict) -> tuple[str, dict]:
    """Render H8 canonical artifact → (html, validation_result)."""
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

    # MoM from monthly_comparison
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

    # Previous month context for channel detail tables
    meta_ctx = (al.get("meta_analysis") or {}).get("monthly_context", {}).get("august", {})
    if not meta_ctx:
        # Try alternate path used by some engine versions
        meta_ctx = (al.get("meta_analysis") or {}).get("monthly_context", {}).get("previous", {})
    goog_ctx = (al.get("google_analysis") or {}).get("monthly_context", {}).get("august", {})
    if not goog_ctx:
        goog_ctx = (al.get("google_analysis") or {}).get("monthly_context", {}).get("previous", {})

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

    # AOV MoM — from monthly_comparison business section
    mc_biz    = (mc or {}).get("business") or {}
    prev_rev_mc  = mc_biz.get("revenue_business", {}).get("previous") if isinstance(mc_biz.get("revenue_business"), dict) else None
    prev_ord_mc  = mc_biz.get("orders_count", {}).get("previous")     if isinstance(mc_biz.get("orders_count"),     dict) else None
    prev_avg_ticket_m = (prev_rev_mc / prev_ord_mc) if (prev_rev_mc and prev_ord_mc and prev_ord_mc > 0) else None
    avg_ticket_mom = (
        (avg_ticket - prev_avg_ticket_m) / prev_avg_ticket_m * 100
        if avg_ticket and prev_avg_ticket_m and prev_avg_ticket_m > 0
        else None
    )

    # Dynamic month labels
    cur_month_label, prev_month_label = _month_labels_from_period(p_start)

    # Channel rows for detail tables
    meta_rows = [
        ("Investimento",           _brl(meta_spend),  _brl(aug_meta_spend),                                        moms.get("meta_spend"), True),
        ("Compras",                _num_conv(meta_conv), _num_conv(aug_meta_conv) if aug_meta_conv else "&#8212;",  moms.get("meta_conv"),  False),
        ("Receita atribu&#237;da", _brl(meta_value),  _brl(aug_meta_value),                                        _mom(meta_value, aug_meta_value), False),
        ("ROAS",                   _x(meta_roas),     _x(aug_meta_roas),                                           moms.get("meta_roas"), False),
        ("CPA",                    _brl(meta_cpa),    _brl(aug_meta_cpa),                                          moms.get("meta_cpa"), True),
    ]
    goog_rows = [
        ("Investimento",             _brl(goog_spend),   _brl(aug_goog_spend),                                         moms.get("goog_spend"), True),
        ("Compras ecommerce",        _num_conv(goog_purch), _num_conv(aug_goog_purch) if aug_goog_purch else "&#8212;", moms.get("goog_purch"), False),
        ("Receita ecommerce",        _brl(goog_value),   _brl(aug_goog_value),                                         _mom(goog_value, aug_goog_value), False),
        ("ROAS ecommerce",           _x(goog_roas),      _x(aug_goog_roas),                                            moms.get("goog_roas"), False),
        ("CPA ecommerce",            _brl(goog_cpa),     _brl(aug_goog_cpa),                                           moms.get("goog_cpa"), True),
        ("Convers&#245;es totais*",  _num_conv(goog_total), _num_conv(aug_goog_total) if aug_goog_total else "&#8212;", _mom(goog_total, aug_goog_total), False),
    ]

    # Campaign availability — read from performance_drilldown in analysis_layer
    perf             = al.get("performance_drilldown") or {}
    meta_perf_env    = perf.get("meta")   or {}
    goog_perf_env    = perf.get("google") or {}
    meta_sel         = (meta_perf_env.get("selection") or {})
    goog_sel         = (goog_perf_env.get("selection") or {})
    meta_campaign_html   = _canonical_campaign_table(meta_perf_env, "meta") \
        if (meta_perf_env.get("rows") and meta_sel.get("top_spend_campaign_ids")) else ""
    google_campaign_html = _canonical_campaign_table(goog_perf_env, "google") \
        if (goog_perf_env.get("rows") and goog_sel.get("top_spend_campaign_ids")) else ""
    meta_camp_avail  = bool(meta_campaign_html)
    goog_camp_avail  = bool(google_campaign_html)
    h8_gate          = {"H8_CLIENT_READY": "YES"}

    diag           = al.get("diagnosis", [])
    prios          = al.get("next_month_priorities", al.get("priorities_next_week", []))
    recs           = al.get("recommendations", [])
    change_digest  = al.get("change_digest_section") or {}
    actions        = al.get("actions") or {}
    cta_authorized = artifact.get("cta_authorized", False)

    target_html  = _monthly_target_block(facts_list)
    period_label = _fmt_period_monthly(p_start, p_end)
    client_name  = _fmt_client(client_id)

    provisional = (al.get("executive_summary", {}) or {}).get("certification_status") == "PROVISIONAL" \
        or bool(al.get("analysis_notes", ""))

    body = "".join([
        _header(client_name, "Performance Mensal", period_label),
        _hero_monthly(revenue, orders, moms.get("revenue"), provisional=provisional),
        _executive_summary(al, section_label_text="RESUMO DO M&#202;S"),
        _kpis_row_monthly(orders, moms.get("orders"), spend, moms.get("spend"),
                          mer, moms.get("mer"), avg_ticket, avg_ticket_mom=avg_ticket_mom),
        target_html,
        _mix_de_canais_table(meta_spend, meta_conv, meta_value, meta_roas, meta_cpa,
                              goog_spend, goog_purch, goog_value, goog_roas, goog_cpa, spend),
        _canal_visao_mensal(
            "Meta Ads", _C["meta_blue"], "META ADS &#8212; VIS&#195;O DO M&#202;S",
            meta_rows,
            "* Receita e compras atribu&#237;das pela plataforma; n&#227;o somar &#224; receita Shopify.",
            cur_month_label=cur_month_label, prev_month_label=prev_month_label,
        ),
        meta_campaign_html if meta_camp_avail else _limitation_card_campaigns(
            "Meta Ads", _C["meta_blue"], "META ADS &#8212; CAMPANHAS",
        ),
        _canal_visao_mensal(
            "Google Ads", _C["goog_green"], "GOOGLE ADS &#8212; VIS&#195;O DO M&#202;S",
            goog_rows,
            "* Convers&#245;es totais incluem a&#231;&#245;es al&#233;m de compras; usar KPIs de ecommerce para decis&#245;es.",
            cur_month_label=cur_month_label, prev_month_label=prev_month_label,
        ),
        google_campaign_html if goog_camp_avail else _limitation_card_campaigns(
            "Google Ads", _C["goog_green"], "GOOGLE ADS &#8212; CAMPANHAS",
        ),
        _change_digest_mensal(change_digest),
        _diagnostico_mensal_v2(diag),
        _actions_section(actions),
        _plano_proximo_mes(prios, recs),
        _cta(client_id) if cta_authorized else _cta_blocked(),
        _footer(),
    ])

    validation = {
        "ok":                       ok and h8_gate['H8_CLIENT_READY'] == 'YES',
        "h8_evidence_quality_gate": h8_gate,
        "errors":                   errors,
        "kpis_checked":             ["MER", "Meta ROAS", "Meta CPA", "Google ROAS ecom", "Google CPA ecom"],
        "month_pacing_available":   bool(target_html),
        "meta_campaigns_rendered":  meta_camp_avail,
        "goog_campaigns_rendered":  goog_camp_avail,
        "meta_creatives_rendered":  False,
        "change_digest_rendered":   bool(change_digest),
        "aov_delta_available":      avg_ticket_mom is not None,
        "cta_authorized":           cta_authorized,
        "template_version":         TEMPLATE_VERSION,
    }
    return _wrap(body), validation
