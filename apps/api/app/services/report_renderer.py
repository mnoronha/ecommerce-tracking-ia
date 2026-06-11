"""
Renders the relatorios-agencia Handlebars templates using pybars3,
then converts to PDF via WeasyPrint.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

def _resolve_templates_dir() -> Path:
    """Find the relatorios-agencia bundle. It now lives inside the API package
    (apps/api/relatorios-agencia) so it ships in the Docker build context; the
    repo-root location is kept as a fallback for older checkouts/local runs."""
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent.parent / "relatorios-agencia",          # apps/api/relatorios-agencia
        here.parent.parent.parent.parent.parent / "relatorios-agencia",  # repo-root (legacy)
    ]
    for c in candidates:
        if (c / "templates" / "mensal.html").exists():
            return c
    return candidates[0]


_TEMPLATES_DIR = _resolve_templates_dir()


# ── Handlebars helpers (mirrors gerar_relatorio.js) ──────────────────────────

def _register_helpers(compiler_instance):
    """pybars3 uses a helpers dict passed at render time, not registration."""
    pass


def _make_helpers() -> dict:
    def _eq(this, a, b):        return a == b
    def _ne(this, a, b):        return a != b
    def _gt(this, a, b):
        try: return float(a) > float(b)
        except (TypeError, ValueError): return False
    def _brl(this, v):
        if v is None: return "—"
        try:
            n = float(v)
            s = f"{abs(n):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            return f"R$ {s}"
        except (TypeError, ValueError): return "—"
    def _num(this, v):
        if v is None: return "—"
        try: return f"{int(float(v)):,}".replace(",", ".")
        except (TypeError, ValueError): return "—"
    def _pct(this, v, *args):
        if v is None: return "—"
        try:
            casas = args[0] if args and isinstance(args[0], int) else 1
            return f"{float(v):.{casas}f}%".replace(".", ",")
        except (TypeError, ValueError): return "—"
    def _roas_helper(this, v):
        if v is None: return "—"
        try: return f"{float(v):.1f}x".replace(".", ",")
        except (TypeError, ValueError): return "—"
    def _inc(this, i):
        try: return int(i) + 1
        except (TypeError, ValueError): return 1
    def _upper(this, s):
        return str(s or "").upper()
    def _unless(this, options, val):
        if not val:
            return options["fn"](this)
        return ""
    def _or(this, *args):
        return any(bool(a) for a in args)

    return {
        "eq":     _eq,
        "ne":     _ne,
        "gt":     _gt,
        "brl":    _brl,
        "num":    _num,
        "pct":    _pct,
        "roas":   _roas_helper,
        "inc":    _inc,
        "upper":  _upper,
        "unless": _unless,
        "or":     _or,
    }


# ── Partials ──────────────────────────────────────────────────────────────────

def _load_partials() -> dict:
    partials_dir = _TEMPLATES_DIR / "templates" / "partials"
    partials = {}
    if partials_dir.exists():
        for f in partials_dir.glob("*.html"):
            partials[f.stem] = f.read_text(encoding="utf-8")
    return partials


# ── CSS loading ───────────────────────────────────────────────────────────────

def _load_css(template_type: str, agencia: dict) -> str:
    css_path = _TEMPLATES_DIR / "assets" / "styles" / f"{template_type}.css"
    if not css_path.exists():
        return ""
    css = css_path.read_text(encoding="utf-8")
    css = css.replace("__COR_PRIMARIA__",   agencia.get("cor_primaria",   "#6c47ff"))
    css = css.replace("__COR_SECUNDARIA__", agencia.get("cor_secundaria", "#a855f7"))
    # WeasyPrint can't fetch remote @import fonts — drop them and map the brand
    # font names to fonts actually installed in the container (Dockerfile ships
    # fonts-liberation + fonts-dejavu-core). Replace ALL occurrences (with or
    # without trailing comma/semicolon) so headings never fall back to an
    # undefined family. Liberation Sans = display/headings, DejaVu Sans = body.
    css = re.sub(r"@import url\([^)]+\);", "", css)
    css = css.replace("'Sora'",  "'Liberation Sans'")
    css = css.replace("'Inter'", "'DejaVu Sans'")
    return css


# ── Main render ───────────────────────────────────────────────────────────────

def render_monthly_html(context: dict) -> str:
    """Render mensal.html Handlebars template → HTML string."""
    try:
        from pybars import Compiler
    except ImportError:
        logger.error("pybars3 not installed — cannot render template")
        return "<html><body><p>pybars3 não instalado.</p></body></html>"

    template_path = _TEMPLATES_DIR / "templates" / "mensal.html"
    if not template_path.exists():
        logger.error("Template not found: %s", template_path)
        return "<html><body><p>Template não encontrado.</p></body></html>"

    agencia = context.get("agencia", {})
    css     = _load_css("mensal", agencia)

    compiler = Compiler()
    partials = _load_partials()

    # Compile partials
    compiled_partials = {}
    for name, src in partials.items():
        try:
            compiled_partials[name] = compiler.compile(src)
        except Exception as exc:
            logger.warning("partial compile failed (%s): %s", name, exc)

    try:
        template_src = template_path.read_text(encoding="utf-8")
        template     = compiler.compile(template_src)
        html = template(
            {**context, "css": css},
            helpers=_make_helpers(),
            partials=compiled_partials,
        )
        html_str = str(html) if not isinstance(html, str) else html
        logger.info("report_renderer: rendered %d chars of HTML", len(html_str))
        return html_str
    except Exception as exc:
        logger.error("template render failed: %s", exc, exc_info=True)
        # Fallback: minimal HTML with key data
        name    = context.get("cliente", {}).get("nome", "")
        mes     = context.get("mes_label", "")
        rev     = context.get("revenue_fmt", "")
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{font-family:Arial;padding:40px}} h1{{color:#1e1b4b}} table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #ccc;padding:8px}}</style>
</head><body>
<h1>Relatório Mensal — {name}</h1><h2>{mes}</h2>
<p>Faturamento: <strong>{rev}</strong></p>
<p><em>Template completo indisponível: {exc}</em></p>
</body></html>"""


def render_to_pdf(context: dict) -> Optional[bytes]:
    """Render template → HTML → PDF bytes. Returns None on failure.
    base_url points at the templates dir so local assets (fonts/images) resolve;
    remote https logos work regardless."""
    html = render_monthly_html(context)
    try:
        from weasyprint import HTML as WP_HTML
        return WP_HTML(string=html, base_url=str(_TEMPLATES_DIR)).write_pdf()
    except Exception as exc:
        logger.error("PDF generation failed: %s", exc)
        return None


def render_monthly_email_html(context: dict) -> str:
    """Dark-theme HTML email for the monthly report (no PDF attachment).

    All CSS is inline, layout uses tables — safe for Gmail, Outlook, Apple Mail.
    """

    def _h(v, fallback: str = "—") -> str:
        return str(v) if v is not None else fallback

    def _sc_color(status: str) -> str:
        return {"verde": "#10b981", "amarelo": "#f59e0b", "vermelho": "#ef4444"}.get(status, "#6366f1")

    def _mom_color(cls: str) -> str:
        if cls == "good":  return "#10b981"
        if cls == "bad":   return "#ef4444"
        return "#64748b"

    def _bar_color(status: str) -> str:
        return {"good": "#10b981", "warn": "#f59e0b", "bad": "#ef4444"}.get(status, "#6366f1")

    KPI_COLORS = ["#10b981", "#6366f1", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"]

    def _kpi_color(i: int) -> str:
        return KPI_COLORS[i % len(KPI_COLORS)]

    agencia   = context.get("agencia") or {}
    cliente   = context.get("cliente") or {}
    scorecard = context.get("scorecard") or {}
    kpis      = context.get("kpis") or []
    metas     = context.get("metas") or []
    canais    = context.get("canais") or []
    produtos  = context.get("top_produtos") or []
    destaques = context.get("destaques") or []
    atencoes  = context.get("atencoes") or []
    plano     = context.get("plano") or {}
    retencao  = context.get("retencao") or {}

    mes_label      = _h(context.get("mes_label"))
    resumo_exec    = _h(context.get("resumo_executivo"), "")
    analise_canais = _h(context.get("analise_canais"), "")
    nome_agencia   = _h(agencia.get("logo_texto") or agencia.get("nome"), "Agência")
    nome_cliente   = _h(cliente.get("nome"), "Cliente")
    cor_primaria   = _h(agencia.get("cor_primaria"), "#6366f1")

    sc_color  = _sc_color(scorecard.get("status", ""))
    sc_emoji  = _h(scorecard.get("emoji"), "📊")
    sc_label  = _h(scorecard.get("label"), "Resultado do Mês")
    sc_verdict = _h(scorecard.get("verdict"), "")

    # ── KPI grid (2 rows × 3 cols) ────────────────────────────────────────
    kpi_rows_html = ""
    for row_start in range(0, max(len(kpis), 1), 3):
        row = kpis[row_start:row_start + 3]
        cells = ""
        for col_idx, k in enumerate(row):
            color     = _kpi_color(row_start + col_idx)
            mom_col   = _mom_color(k.get("momClass", "flat"))
            mom       = k.get("mom", "")
            pad_left  = "0" if col_idx == 0 else "4px"
            pad_right = "0" if col_idx == 2 else "4px"
            cells += (
                f'<td width="33%" style="padding:0 {pad_right} 8px {pad_left};vertical-align:top">'
                f'<div style="background:#1a1f2e;border:1px solid #2a2f3e;border-top:2px solid {color};'
                f'border-radius:6px;padding:14px 12px">'
                f'<p style="margin:0 0 6px;font-size:11px;color:#64748b;text-transform:uppercase;'
                f'letter-spacing:0.4px">{_h(k.get("label"))}</p>'
                f'<p style="margin:0;font-size:20px;font-weight:700;color:#e2e8f0">{_h(k.get("valor"))}</p>'
                + (f'<p style="margin:4px 0 0;font-size:11px;color:{mom_col}">{mom}</p>' if mom else "")
                + "</div></td>"
            )
        # Pad to 3 columns
        for _ in range(3 - len(row)):
            cells += '<td width="33%" style="padding:0 4px 8px"></td>'
        kpi_rows_html += f"<tr>{cells}</tr>"

    # ── Metas / Goals ────────────────────────────────────────────────────
    metas_rows = ""
    for meta in metas:
        bar_pct   = min(float(meta.get("bar_pct") or 0), 100)
        bc        = _bar_color(meta.get("status", "warn"))
        metas_rows += (
            f'<tr>'
            f'<td style="padding:7px 8px 7px 0;color:#94a3b8;font-size:13px;white-space:nowrap">'
            f'{_h(meta.get("label"))}</td>'
            f'<td style="padding:7px 4px;width:200px">'
            f'<div style="background:#2a2f3e;border-radius:4px;height:5px">'
            f'<div style="background:{bc};border-radius:4px;height:5px;width:{bar_pct:.0f}%"></div>'
            f'</div></td>'
            f'<td style="padding:7px 0 7px 8px;text-align:right;font-size:13px;color:#e2e8f0;white-space:nowrap">'
            f'{_h(meta.get("realizado"))}</td>'
            f'<td style="padding:7px 0;text-align:right;font-size:11px;color:#64748b;white-space:nowrap">'
            f'/ {_h(meta.get("meta"))}</td>'
            f'<td style="padding:7px 0 7px 8px;text-align:right;font-size:11px;color:{bc};white-space:nowrap">'
            f'{_h(meta.get("pct_fmt"))}</td>'
            f'</tr>'
        )

    # ── Canais table ─────────────────────────────────────────────────────
    canal_rows = ""
    for ch in canais[:6]:
        icone = _h(ch.get("icone"), "📡")
        nome  = _h(ch.get("nome"))
        canal_rows += (
            f'<tr>'
            f'<td style="padding:9px 8px 9px 0;border-bottom:1px solid #2a2f3e;'
            f'color:#e2e8f0;font-size:13px">{icone} {nome}</td>'
            f'<td style="padding:9px 8px;border-bottom:1px solid #2a2f3e;'
            f'text-align:right;color:#94a3b8;font-size:13px">{_h(ch.get("investimento_fmt"))}</td>'
            f'<td style="padding:9px 8px;border-bottom:1px solid #2a2f3e;'
            f'text-align:right;color:#e2e8f0;font-size:13px">{_h(ch.get("receita_fmt"))}</td>'
            f'<td style="padding:9px 8px;border-bottom:1px solid #2a2f3e;'
            f'text-align:right;color:#10b981;font-size:13px;font-weight:600">{_h(ch.get("roas_fmt"))}</td>'
            f'<td style="padding:9px 0 9px 8px;border-bottom:1px solid #2a2f3e;'
            f'text-align:right;color:#94a3b8;font-size:13px">{_h(ch.get("cpa_fmt"))}</td>'
            f'</tr>'
        )

    # ── Top produtos ─────────────────────────────────────────────────────
    produto_rows = ""
    for p in produtos[:5]:
        produto_rows += (
            f'<tr>'
            f'<td style="padding:7px 8px 7px 0;color:#64748b;font-size:13px;width:20px">'
            f'{_h(p.get("rank"))}</td>'
            f'<td style="padding:7px 4px;color:#e2e8f0;font-size:13px">{_h(p.get("name"))}</td>'
            f'<td style="padding:7px 8px;text-align:right;color:#10b981;font-size:13px;white-space:nowrap">'
            f'{_h(p.get("revenue_fmt"))}</td>'
            f'<td style="padding:7px 0;text-align:right;color:#64748b;font-size:12px;white-space:nowrap">'
            f'{_h(p.get("qty_fmt"))} un</td>'
            f'</tr>'
        )

    # ── Destaques + Atenções (IA) ─────────────────────────────────────────
    insights_html = ""
    for d in destaques[:4]:
        tipo  = d.get("tipo", "destaque")
        color = "#10b981" if tipo == "destaque" else "#f59e0b"
        titulo = _h(d.get("titulo"))
        texto  = _h(d.get("texto"), "")
        insights_html += (
            f'<div style="background:#1a1f2e;border-left:3px solid {color};'
            f'border-radius:0 6px 6px 0;padding:10px 14px;margin-bottom:8px">'
            f'<p style="margin:0 0 2px;font-size:10px;color:{color};'
            f'text-transform:uppercase;letter-spacing:0.5px">{tipo}</p>'
            f'<p style="margin:0;font-size:13px;font-weight:600;color:#e2e8f0">{titulo}</p>'
            + (f'<p style="margin:4px 0 0;font-size:13px;color:#94a3b8">{texto}</p>' if texto else "")
            + "</div>"
        )
    for a in atencoes[:2]:
        titulo = _h(a.get("titulo"))
        texto  = _h(a.get("texto"), "")
        insights_html += (
            f'<div style="background:#1a1f2e;border-left:3px solid #ef4444;'
            f'border-radius:0 6px 6px 0;padding:10px 14px;margin-bottom:8px">'
            f'<p style="margin:0 0 2px;font-size:10px;color:#ef4444;'
            f'text-transform:uppercase;letter-spacing:0.5px">atenção</p>'
            f'<p style="margin:0;font-size:13px;font-weight:600;color:#e2e8f0">{titulo}</p>'
            + (f'<p style="margin:4px 0 0;font-size:13px;color:#94a3b8">{texto}</p>' if texto else "")
            + "</div>"
        )

    # ── Análise de canais (IA) ────────────────────────────────────────────
    analise_block = ""
    if analise_canais:
        analise_block = (
            f'<p style="margin:0;font-size:13px;color:#94a3b8;line-height:1.6">'
            f'{analise_canais}</p>'
        )

    # ── Resumo executivo (IA) ─────────────────────────────────────────────
    resumo_block = ""
    if resumo_exec:
        resumo_block = (
            f'<p style="margin:0;font-size:13px;color:#94a3b8;line-height:1.6">'
            f'{resumo_exec}</p>'
        )

    # ── Plano próximo mês ─────────────────────────────────────────────────
    plano_block = ""
    if plano:
        acoes = plano.get("acoes") or []
        meta_fat  = _h(plano.get("meta_faturamento_fmt"))
        meta_roas = _h(plano.get("meta_roas_fmt"))
        budget    = _h(plano.get("budget_fmt"))
        acoes_html = "".join(
            f'<p style="margin:0 0 5px;font-size:13px;color:#94a3b8">'
            f'<span style="color:{cor_primaria};font-weight:700">{i+1}.</span> {acao}</p>'
            for i, acao in enumerate(acoes[:5])
        )
        plano_block = (
            f'<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:14px">'
            f'<tr>'
            f'<td width="33%" style="padding-right:8px">'
            f'<p style="margin:0 0 2px;font-size:11px;color:#64748b">Meta Faturamento</p>'
            f'<p style="margin:0;font-size:17px;font-weight:700;color:#e2e8f0">{meta_fat}</p>'
            f'</td>'
            f'<td width="33%" style="padding-right:8px">'
            f'<p style="margin:0 0 2px;font-size:11px;color:#64748b">Meta ROAS</p>'
            f'<p style="margin:0;font-size:17px;font-weight:700;color:#e2e8f0">{meta_roas}</p>'
            f'</td>'
            f'<td width="33%">'
            f'<p style="margin:0 0 2px;font-size:11px;color:#64748b">Budget Total</p>'
            f'<p style="margin:0;font-size:17px;font-weight:700;color:#e2e8f0">{budget}</p>'
            f'</td>'
            f'</tr>'
            f'</table>'
            + acoes_html
        )

    # ── Retenção ──────────────────────────────────────────────────────────
    retencao_block = ""
    if retencao and retencao.get("novos") is not None:
        novos  = retencao.get("novos", 0) or 0
        recorr = retencao.get("recorrentes", 0) or 0
        taxa   = _h(retencao.get("taxa_recorrencia_fmt"))
        retencao_block = (
            f'<table width="100%" cellpadding="0" cellspacing="0">'
            f'<tr>'
            f'<td width="50%" style="padding-right:6px">'
            f'<div style="background:#1a1f2e;border:1px solid #2a2f3e;border-radius:8px;'
            f'padding:14px;text-align:center">'
            f'<p style="margin:0 0 3px;font-size:22px;font-weight:700;color:#e2e8f0">{novos}</p>'
            f'<p style="margin:0;font-size:12px;color:#64748b">Novos clientes</p>'
            f'</div>'
            f'</td>'
            f'<td width="50%" style="padding-left:6px">'
            f'<div style="background:#1a1f2e;border:1px solid #2a2f3e;border-radius:8px;'
            f'padding:14px;text-align:center">'
            f'<p style="margin:0 0 3px;font-size:22px;font-weight:700;color:#10b981">{recorr}</p>'
            f'<p style="margin:0;font-size:12px;color:#64748b">Recorrentes · {taxa}</p>'
            f'</div>'
            f'</td>'
            f'</tr>'
            f'</table>'
        )

    # ── Section builder ───────────────────────────────────────────────────
    def _section(title: str, body: str) -> str:
        if not body:
            return ""
        return (
            f'<tr><td style="padding:0 0 28px">'
            f'<p style="margin:0 0 12px;font-size:15px;font-weight:600;color:#e2e8f0;'
            f'border-bottom:1px solid #2a2f3e;padding-bottom:8px">{title}</p>'
            f'{body}'
            f'</td></tr>'
        )

    site_agencia = _h(agencia.get("site"), "")

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Relatório Mensal · {mes_label} · {nome_cliente}</title>
</head>
<body style="margin:0;padding:0;background:#0c0e14;font-family:Arial,Helvetica,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0c0e14">
<tr><td align="center" style="padding:24px 8px 32px">

<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%">

  <!-- HEADER -->
  <tr>
    <td style="background:#1a1f2e;border-radius:12px 12px 0 0;padding:24px 28px;
               border-bottom:1px solid #2a2f3e">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td>
            <p style="margin:0;font-size:13px;color:{cor_primaria};font-weight:600;
               text-transform:uppercase;letter-spacing:0.8px">{nome_agencia}</p>
            <p style="margin:4px 0 0;font-size:22px;font-weight:700;color:#f1f5f9">{nome_cliente}</p>
          </td>
          <td align="right">
            <div style="background:#0c0e14;border:1px solid #2a2f3e;border-radius:8px;
                        padding:8px 14px;display:inline-block">
              <p style="margin:0;font-size:11px;color:#64748b">Relatório Mensal</p>
              <p style="margin:2px 0 0;font-size:15px;font-weight:700;color:#e2e8f0">{mes_label}</p>
            </div>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- SCORECARD BANNER -->
  <tr>
    <td style="background:{sc_color}18;border-left:4px solid {sc_color};
               padding:16px 28px;border-top:none">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td>
            <p style="margin:0 0 2px;font-size:20px">{sc_emoji} <span style="font-size:15px;font-weight:700;color:{sc_color}">{sc_label}</span></p>
            {f'<p style="margin:0;font-size:13px;color:#94a3b8">{sc_verdict}</p>' if sc_verdict else ''}
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- MAIN BODY -->
  <tr>
    <td style="background:#141720;padding:28px 28px 8px;border-radius:0 0 12px 12px">
      <table width="100%" cellpadding="0" cellspacing="0">

        <!-- Resumo executivo -->
        {_section("Resumo Executivo", resumo_block)}

        <!-- KPIs -->
        {_section("KPIs do Mês", f'<table width="100%" cellpadding="0" cellspacing="0">{kpi_rows_html}</table>')}

        <!-- Metas -->
        {_section("Metas vs Realizado",
            f'<table width="100%" cellpadding="0" cellspacing="0">{metas_rows}</table>'
        ) if metas_rows else ""}

        <!-- Canais -->
        {_section("Desempenho por Canal",
            f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">'
            f'<thead><tr>'
            f'<th align="left" style="padding:0 8px 8px 0;font-size:11px;color:#64748b;font-weight:400;text-transform:uppercase">Canal</th>'
            f'<th align="right" style="padding:0 8px 8px;font-size:11px;color:#64748b;font-weight:400;text-transform:uppercase">Invest.</th>'
            f'<th align="right" style="padding:0 8px 8px;font-size:11px;color:#64748b;font-weight:400;text-transform:uppercase">Receita</th>'
            f'<th align="right" style="padding:0 8px 8px;font-size:11px;color:#64748b;font-weight:400;text-transform:uppercase">ROAS</th>'
            f'<th align="right" style="padding:0 0 8px 8px;font-size:11px;color:#64748b;font-weight:400;text-transform:uppercase">CPA</th>'
            f'</tr></thead>'
            f'<tbody>{canal_rows}</tbody>'
            f'</table>'
            + (f'<p style="margin:10px 0 0;font-size:12px;color:#475569;line-height:1.5">{analise_block}</p>' if analise_canais else "")
        ) if canal_rows else ""}

        <!-- Top Produtos -->
        {_section("Top Produtos",
            f'<table width="100%" cellpadding="0" cellspacing="0">{produto_rows}</table>'
        ) if produto_rows else ""}

        <!-- Retenção -->
        {_section("Retenção de Clientes", retencao_block) if retencao_block else ""}

        <!-- Insights -->
        {_section("Destaques &amp; Atenções", insights_html) if insights_html else ""}

        <!-- Plano -->
        {_section("Plano para o Próximo Mês", plano_block) if plano_block else ""}

        <!-- Footer -->
        <tr>
          <td style="padding:8px 0 24px;border-top:1px solid #2a2f3e">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td>
                  <p style="margin:0;font-size:12px;color:#475569">
                    {nome_agencia}{f' · <a href="{site_agencia}" style="color:{cor_primaria};text-decoration:none">{site_agencia}</a>' if site_agencia else ""}
                  </p>
                  <p style="margin:4px 0 0;font-size:11px;color:#374151">
                    Relatório gerado automaticamente para {nome_cliente} · {mes_label}
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>

      </table>
    </td>
  </tr>

</table>

</td></tr>
</table>
</body>
</html>"""
