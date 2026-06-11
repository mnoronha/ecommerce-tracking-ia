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


def render_monthly_email_html(context: dict) -> str:  # noqa: C901
    """Dark-theme HTML email for the monthly report (no PDF attachment).

    All CSS is inline, layout uses tables — safe for Gmail, Outlook, Apple Mail.

    Sections (in order):
      1  Header (agency + client + month)
      2  Scorecard banner
      3  Resumo executivo (AI)
      4  KPIs do mês (6 cards 2×3)
      5  Comparativo mensal + anual
      6  Metas vs Realizado
      7  Desempenho por canal (server-side attribution)
      8  Análise dos canais (AI)
      9  Campanhas Meta Ads   (platform-reported)
      10 Campanhas Google Ads (platform-reported)
      11 Atribuição por canal (revenue share bars)
      12 Funil de conversão
      13 Eficiência de mídia (MER / CAC / LTV)
      14 Top produtos
      15 Retenção de clientes
      16 Destaques & Atenções (AI)
      17 Plano próximo mês (AI)
      18 Footer
    """

    # ── Helpers ───────────────────────────────────────────────────────────
    def _h(v, fallback: str = "—") -> str:
        return str(v) if v is not None else fallback

    def _fmt_n(v) -> str:
        try:
            return f"{int(float(v)):,}".replace(",", ".")
        except (TypeError, ValueError):
            return "—"

    def _sc_color(status: str) -> str:
        return {"verde": "#10b981", "amarelo": "#f59e0b", "vermelho": "#ef4444"}.get(status, "#6366f1")

    def _dc(cls: str) -> str:  # delta color
        if cls == "good": return "#10b981"
        if cls == "bad":  return "#ef4444"
        return "#64748b"

    def _bc(status: str) -> str:  # bar color
        return {"good": "#10b981", "warn": "#f59e0b", "bad": "#ef4444"}.get(status, "#6366f1")

    KPI_COLORS = ["#10b981", "#6366f1", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"]

    # ── Context extraction ────────────────────────────────────────────────
    agencia             = context.get("agencia") or {}
    cliente             = context.get("cliente") or {}
    scorecard           = context.get("scorecard") or {}
    kpis                = context.get("kpis") or []
    yoy_rows            = context.get("yoy") or []
    metas               = context.get("metas") or []
    canais              = context.get("canais") or []
    campanhas_por_canal = context.get("campanhas_por_canal") or []
    atribuicao          = context.get("atribuicao") or []
    funil               = context.get("funil") or []
    eficiencia          = context.get("eficiencia") or {}
    produtos            = context.get("top_produtos") or []
    destaques           = context.get("destaques") or []
    atencoes            = context.get("atencoes") or []
    plano               = context.get("plano") or {}
    retencao            = context.get("retencao") or {}

    # plano meta/budget live in separate context keys (not inside plano dict)
    plano_fat_fmt  = _h(context.get("plano_meta_faturamento_fmt"), "—")
    plano_bud_fmt  = _h(context.get("plano_budget_fmt"), "—")
    plano_roas_fmt = _h(plano.get("meta_roas"), "—")   # already formatted string

    mes_label      = _h(context.get("mes_label"))
    mes_ant_label  = _h(context.get("mes_anterior_label"), "mês ant.")
    ano_ant_label  = _h(context.get("ano_anterior_label"), "ano ant.")
    resumo_exec    = _h(context.get("resumo_executivo"), "")
    analise_canais = _h(context.get("analise_canais"), "")
    nome_agencia   = _h(agencia.get("logo_texto") or agencia.get("nome"), "Agência")
    nome_cliente   = _h(cliente.get("nome"), "Cliente")
    cor_primaria   = _h(agencia.get("cor_primaria"), "#6366f1")
    site_agencia   = _h(agencia.get("site"), "")

    sc_color   = _sc_color(scorecard.get("status", ""))
    sc_emoji   = _h(scorecard.get("emoji"), "📊")
    sc_label   = _h(scorecard.get("label"), "Resultado do Mês")
    sc_verdict = _h(scorecard.get("verdict"), "")

    # ── Section builder ───────────────────────────────────────────────────
    def _section(title: str, body: str, note: str = "") -> str:
        if not body:
            return ""
        note_html = (
            f'<p style="margin:0 0 10px;font-size:11px;color:#475569;'
            f'font-style:italic">{note}</p>'
        ) if note else ""
        return (
            f'<tr><td style="padding:0 0 28px">'
            f'<p style="margin:0 0 12px;font-size:15px;font-weight:600;color:#e2e8f0;'
            f'border-bottom:1px solid #2a2f3e;padding-bottom:8px">{title}</p>'
            f'{note_html}{body}'
            f'</td></tr>'
        )

    def _th(label: str, align: str = "right") -> str:
        return (
            f'<th align="{align}" style="padding:0 6px 8px;font-size:10px;color:#64748b;'
            f'font-weight:400;text-transform:uppercase;white-space:nowrap">{label}</th>'
        )

    # ── 1. KPI grid (2 rows × 3 cols) ────────────────────────────────────
    kpi_rows_html = ""
    for row_start in range(0, max(len(kpis), 1), 3):
        row = kpis[row_start:row_start + 3]
        cells = ""
        for col_idx, k in enumerate(row):
            color    = KPI_COLORS[(row_start + col_idx) % len(KPI_COLORS)]
            mom_col  = _dc(k.get("momClass", "flat"))
            yoy_col  = _dc(k.get("yoyClass", "flat"))
            mom      = k.get("mom", "")
            yoy_d    = k.get("yoy", "")
            pad_l    = "0" if col_idx == 0 else "4px"
            pad_r    = "0" if col_idx == 2 else "4px"
            cells += (
                f'<td width="33%" style="padding:0 {pad_r} 8px {pad_l};vertical-align:top">'
                f'<div style="background:#1a1f2e;border:1px solid #2a2f3e;'
                f'border-top:2px solid {color};border-radius:6px;padding:12px">'
                f'<p style="margin:0 0 4px;font-size:10px;color:#64748b;'
                f'text-transform:uppercase;letter-spacing:0.4px">{_h(k.get("label"))}</p>'
                f'<p style="margin:0 0 5px;font-size:19px;font-weight:700;color:#e2e8f0">'
                f'{_h(k.get("valor"))}</p>'
                + (f'<p style="margin:0;font-size:11px;color:{mom_col}">'
                   f'vs {mes_ant_label[:3]}: {mom}</p>' if mom else "")
                + (f'<p style="margin:1px 0 0;font-size:10px;color:{yoy_col}">'
                   f'vs {ano_ant_label[:7]}: {yoy_d}</p>' if yoy_d else "")
                + "</div></td>"
            )
        for _ in range(3 - len(row)):
            cells += '<td width="33%" style="padding:0 4px 8px"></td>'
        kpi_rows_html += f"<tr>{cells}</tr>"

    # ── 2. Comparativo mensal + anual ─────────────────────────────────────
    # Uses yoy_rows from context (label, atual, ant, delta, classe)
    comp_rows = ""
    for r in yoy_rows:
        dc = _dc(r.get("classe", "flat"))
        comp_rows += (
            f'<tr>'
            f'<td style="padding:8px 8px 8px 0;color:#94a3b8;font-size:13px">{_h(r.get("label"))}</td>'
            f'<td style="padding:8px;text-align:right;color:#e2e8f0;font-size:13px;font-weight:600">'
            f'{_h(r.get("atual"))}</td>'
            f'<td style="padding:8px;text-align:right;color:#64748b;font-size:13px">'
            f'{_h(r.get("ant"))}</td>'
            f'<td style="padding:8px 0;text-align:right;font-size:13px;color:{dc};white-space:nowrap">'
            f'{_h(r.get("delta"))}</td>'
            f'</tr>'
        )
    comp_block = ""
    if comp_rows:
        comp_block = (
            f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">'
            f'<thead><tr>'
            f'{_th("Indicador", "left")}'
            f'{_th(mes_label)}'
            f'{_th(ano_ant_label)}'
            f'{_th("Variação")}'
            f'</tr></thead>'
            f'<tbody>{comp_rows}</tbody>'
            f'</table>'
        )

    # ── 3. Metas / Goals ─────────────────────────────────────────────────
    metas_rows = ""
    for meta in metas:
        bar_pct = min(float(meta.get("bar_pct") or 0), 100)
        bc      = _bc(meta.get("status", "warn"))
        metas_rows += (
            f'<tr>'
            f'<td style="padding:7px 8px 7px 0;color:#94a3b8;font-size:13px;white-space:nowrap">'
            f'{_h(meta.get("label"))}</td>'
            f'<td style="padding:7px 4px">'
            f'<div style="background:#2a2f3e;border-radius:4px;height:5px">'
            f'<div style="background:{bc};border-radius:4px;height:5px;width:{bar_pct:.0f}%"></div>'
            f'</div></td>'
            f'<td style="padding:7px 0 7px 8px;text-align:right;font-size:13px;color:#e2e8f0;white-space:nowrap">'
            f'{_h(meta.get("realizado"))}</td>'
            f'<td style="padding:7px 0;text-align:right;font-size:11px;color:#64748b;white-space:nowrap">'
            f'/ {_h(meta.get("meta"))}</td>'
            f'<td style="padding:7px 0 7px 6px;text-align:right;font-size:12px;color:{bc};white-space:nowrap;font-weight:600">'
            f'{_h(meta.get("pct_fmt"))}</td>'
            f'</tr>'
        )

    # ── 4. Canais (server-side attribution) ──────────────────────────────
    # Summary row + per-channel metrics with MoM deltas
    canal_block = ""
    if canais:
        # Header table
        canal_rows = ""
        for ch in canais[:5]:
            canal_rows += (
                f'<tr>'
                f'<td style="padding:9px 6px 9px 0;border-bottom:1px solid #2a2f3e;'
                f'color:#e2e8f0;font-size:13px">'
                f'{_h(ch.get("icone"),"📡")} {_h(ch.get("nome"))}</td>'
                f'<td style="padding:9px 6px;border-bottom:1px solid #2a2f3e;'
                f'text-align:right;color:#94a3b8;font-size:13px;white-space:nowrap">'
                f'{_h(ch.get("investimento_fmt"))}</td>'
                f'<td style="padding:9px 6px;border-bottom:1px solid #2a2f3e;'
                f'text-align:right;color:#e2e8f0;font-size:13px;white-space:nowrap">'
                f'{_h(ch.get("receita_fmt"))}</td>'
                f'<td style="padding:9px 6px;border-bottom:1px solid #2a2f3e;'
                f'text-align:right;color:#10b981;font-size:13px;font-weight:700;white-space:nowrap">'
                f'{_h(ch.get("roas_fmt"))}</td>'
                f'<td style="padding:9px 6px;border-bottom:1px solid #2a2f3e;'
                f'text-align:right;color:#94a3b8;font-size:13px;white-space:nowrap">'
                f'{_fmt_n(ch.get("pedidos"))}</td>'
                f'<td style="padding:9px 0 9px 6px;border-bottom:1px solid #2a2f3e;'
                f'text-align:right;color:#94a3b8;font-size:13px;white-space:nowrap">'
                f'{_h(ch.get("cpa_fmt"))}</td>'
                f'</tr>'
            )

        canal_block = (
            f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">'
            f'<thead><tr>'
            f'{_th("Canal","left")}{_th("Invest.")}{_th("Receita")}'
            f'{_th("ROAS")}{_th("Pedidos")}{_th("CPA")}'
            f'</tr></thead>'
            f'<tbody>{canal_rows}</tbody>'
            f'</table>'
        )

        # Per-channel deep dive with MoM deltas (uses metricas list)
        detail_blocks = ""
        for ch in canais[:3]:
            metricas = ch.get("metricas") or []
            if not metricas:
                continue
            met_html = ""
            for m in metricas:
                dcol = _dc(m.get("classe", "flat"))
                delta = m.get("delta", "")
                met_html += (
                    f'<td style="padding:8px 6px;text-align:center;vertical-align:top;width:16%">'
                    f'<p style="margin:0 0 2px;font-size:10px;color:#64748b;text-transform:uppercase">'
                    f'{_h(m.get("label"))}</p>'
                    f'<p style="margin:0;font-size:13px;font-weight:600;color:#e2e8f0">{_h(m.get("valor"))}</p>'
                    + (f'<p style="margin:1px 0 0;font-size:10px;color:{dcol}">{delta}</p>' if delta else "")
                    + f'</td>'
                )
            icone = _h(ch.get("icone"), "📡")
            nome  = _h(ch.get("nome"))
            detail_blocks += (
                f'<div style="background:#1a1f2e;border:1px solid #2a2f3e;border-radius:8px;'
                f'padding:12px 14px;margin-top:10px">'
                f'<p style="margin:0 0 8px;font-size:12px;font-weight:600;color:#94a3b8">'
                f'{icone} {nome} — detalhamento vs {mes_ant_label}</p>'
                f'<table width="100%" cellpadding="0" cellspacing="0"><tr>{met_html}</tr></table>'
                f'</div>'
            )
        canal_block += detail_blocks

    # ── 5. Campanhas por canal (platform-reported) ────────────────────────
    camp_blocks = ""
    for grp in campanhas_por_canal[:3]:
        cname   = _h(grp.get("canal_label"))
        cicone  = _h(grp.get("icone"), "📊")
        camp_list = grp.get("campanhas") or []
        if not camp_list:
            continue
        rows = ""
        for c in camp_list[:5]:
            st = c.get("status", "")
            st_color = "#10b981" if st == "ativo" else "#64748b"
            rows += (
                f'<tr>'
                f'<td style="padding:8px 6px 8px 0;border-bottom:1px solid #1e2435;'
                f'color:#e2e8f0;font-size:12px;max-width:180px">'
                f'<span style="display:block;overflow:hidden;white-space:nowrap;'
                f'text-overflow:ellipsis">{_h(c.get("nome"))}</span>'
                f'<span style="font-size:10px;color:{st_color}">{_h(c.get("status_label"),"—")}</span>'
                f'</td>'
                f'<td style="padding:8px 6px;border-bottom:1px solid #1e2435;'
                f'text-align:right;color:#94a3b8;font-size:12px;white-space:nowrap">{_h(c.get("investimento_fmt"))}</td>'
                f'<td style="padding:8px 6px;border-bottom:1px solid #1e2435;'
                f'text-align:right;color:#e2e8f0;font-size:12px;white-space:nowrap">{_h(c.get("resultado_fmt"))}</td>'
                f'<td style="padding:8px 6px;border-bottom:1px solid #1e2435;'
                f'text-align:right;color:#10b981;font-size:12px;font-weight:700;white-space:nowrap">{_h(c.get("indice_fmt"))}</td>'
                f'<td style="padding:8px 6px;border-bottom:1px solid #1e2435;'
                f'text-align:right;color:#94a3b8;font-size:12px;white-space:nowrap">{_h(c.get("qtd_fmt"))}</td>'
                f'<td style="padding:8px 0 8px 6px;border-bottom:1px solid #1e2435;'
                f'text-align:right;color:#94a3b8;font-size:12px;white-space:nowrap">{_h(c.get("custo_fmt"))}</td>'
                f'</tr>'
            )
        camp_blocks += (
            f'<div style="background:#1a1f2e;border:1px solid #2a2f3e;border-radius:8px;'
            f'padding:14px;margin-bottom:12px">'
            f'<p style="margin:0 0 10px;font-size:13px;font-weight:600;color:#e2e8f0">'
            f'{cicone} {cname}</p>'
            f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">'
            f'<thead><tr>'
            f'{_th("Campanha","left")}{_th("Invest.")}{_th("Receita")}'
            f'{_th("ROAS")}{_th("Conv.")}{_th("CPA")}'
            f'</tr></thead>'
            f'<tbody>{rows}</tbody>'
            f'</table>'
            f'</div>'
        )

    # ── 6. Atribuição por canal (server-side, receita por origem) ─────────
    atr_rows = ""
    atr_total = sum(float(a.get("receita") or 0) for a in atribuicao)
    PAID_CANALS = {"Meta Ads", "Google Ads", "TikTok Ads"}
    paid_rev = sum(
        float(a.get("receita") or 0) for a in atribuicao
        if a.get("canal") in PAID_CANALS
    )
    coverage_pct = round(paid_rev / atr_total * 100, 1) if atr_total > 0 else 0
    CANAL_COLORS = {
        "Meta Ads": "#0866ff", "Google Ads": "#ea4335",
        "TikTok Ads": "#010101", "Email": "#6366f1",
        "Orgânico": "#10b981", "Direto": "#64748b",
    }
    for a in atribuicao[:8]:
        rev   = float(a.get("receita") or 0)
        share = round(rev / atr_total * 100, 1) if atr_total > 0 else 0
        canal = a.get("canal", "")
        bar_c = CANAL_COLORS.get(canal, cor_primaria)
        peds  = a.get("pedidos", 0)
        atr_rows += (
            f'<tr>'
            f'<td style="padding:7px 8px 7px 0;color:#e2e8f0;font-size:13px;white-space:nowrap">'
            f'{canal}</td>'
            f'<td style="padding:7px 6px;width:35%">'
            f'<div style="background:#2a2f3e;border-radius:3px;height:6px">'
            f'<div style="background:{bar_c};border-radius:3px;height:6px;'
            f'width:{min(share,100):.0f}%"></div>'
            f'</div></td>'
            f'<td style="padding:7px 6px;text-align:right;color:#e2e8f0;font-size:13px;'
            f'font-weight:600;white-space:nowrap">{_h(a.get("receita_fmt"))}</td>'
            f'<td style="padding:7px 0;text-align:right;color:#64748b;font-size:12px;white-space:nowrap">'
            f'{share:.1f}% · {_fmt_n(peds)} ped.</td>'
            f'</tr>'
        )

    # ── 7. Funil de conversão ─────────────────────────────────────────────
    funil_rows = ""
    for stage in funil:
        bar = float(stage.get("bar_pct") or 0)
        conv = stage.get("conv_fmt")
        funil_rows += (
            f'<tr>'
            f'<td style="padding:7px 8px 7px 0;color:#94a3b8;font-size:13px;white-space:nowrap;width:170px">'
            f'{_h(stage.get("label"))}</td>'
            f'<td style="padding:7px 6px">'
            f'<div style="background:#2a2f3e;border-radius:3px;height:7px">'
            f'<div style="background:{cor_primaria};border-radius:3px;height:7px;width:{bar:.0f}%"></div>'
            f'</div></td>'
            f'<td style="padding:7px 0 7px 8px;text-align:right;color:#e2e8f0;font-size:13px;white-space:nowrap">'
            f'{_h(stage.get("n_fmt"))}</td>'
            + (f'<td style="padding:7px 0 7px 6px;text-align:right;font-size:11px;color:#64748b;white-space:nowrap">'
               f'→ {conv}</td>' if conv else '<td></td>')
            + f'</tr>'
        )

    # ── 8. Eficiência de mídia ────────────────────────────────────────────
    ef_block = ""
    if eficiencia:
        ltv_cac_ok = eficiencia.get("ltv_cac_ok")
        ltv_col    = "#10b981" if ltv_cac_ok else "#f59e0b"

        def _ef_card(label, val, sub="", col="#e2e8f0"):
            return (
                f'<td style="padding:0 4px;vertical-align:top">'
                f'<div style="background:#1a1f2e;border:1px solid #2a2f3e;border-radius:8px;'
                f'padding:12px;text-align:center">'
                f'<p style="margin:0 0 2px;font-size:10px;color:#64748b;text-transform:uppercase">{label}</p>'
                f'<p style="margin:0;font-size:17px;font-weight:700;color:{col}">{val}</p>'
                + (f'<p style="margin:2px 0 0;font-size:10px;color:#475569">{sub}</p>' if sub else "")
                + f'</div></td>'
            )

        mer   = _h(eficiencia.get("mer_fmt"))
        cac   = _h(eficiencia.get("cac_fmt"))
        ltv   = _h(eficiencia.get("ltv_fmt")) if eficiencia.get("ltv") else "—"
        ratio = _h(eficiencia.get("ltv_cac_fmt")) if eficiencia.get("ltv_cac") else "—"

        ef_block = (
            f'<table width="100%" cellpadding="0" cellspacing="0">'
            f'<tr>'
            f'{_ef_card("MER (ROAS geral)", mer, "receita/invest total")}'
            f'{_ef_card("CAC", cac, "custo por novo cliente")}'
            f'{_ef_card("LTV estimado", ltv)}'
            f'{_ef_card("LTV:CAC", ratio, "meta: ≥ 3:1", ltv_col)}'
            f'</tr>'
            f'</table>'
        )

    # ── 9. Top produtos ───────────────────────────────────────────────────
    produto_rows = ""
    for p in produtos[:8]:
        produto_rows += (
            f'<tr>'
            f'<td style="padding:7px 6px 7px 0;color:#64748b;font-size:12px;width:20px">'
            f'{_h(p.get("rank"))}</td>'
            f'<td style="padding:7px 4px;color:#e2e8f0;font-size:13px">{_h(p.get("name"))}</td>'
            f'<td style="padding:7px 6px;text-align:right;color:#10b981;font-size:13px;'
            f'white-space:nowrap;font-weight:600">{_h(p.get("revenue_fmt"))}</td>'
            f'<td style="padding:7px 0;text-align:right;color:#64748b;font-size:12px;white-space:nowrap">'
            f'{_h(p.get("qty_fmt"))} un</td>'
            f'</tr>'
        )

    # ── 10. Retenção ──────────────────────────────────────────────────────
    retencao_block = ""
    if retencao and retencao.get("total", 0):
        novos  = retencao.get("novos", 0) or 0
        recorr = retencao.get("recorrentes", 0) or 0
        total  = retencao.get("total", novos + recorr) or 1
        taxa   = _h(retencao.get("rep_rate_fmt"))       # ← chave correta
        novos_pct  = round(novos  / total * 100)
        recorr_pct = round(recorr / total * 100)
        rev_nov    = _h(retencao.get("rev_novos_fmt"))
        rev_rec    = _h(retencao.get("rev_rec_fmt"))
        tkt_nov    = _h(retencao.get("ticket_novo_fmt"))
        tkt_rec    = _h(retencao.get("ticket_rec_fmt"))

        def _ret_card(count, pct, color, label, sub_rev, sub_tkt):
            return (
                f'<td width="50%" style="padding-right:6px;vertical-align:top">'
                f'<div style="background:#1a1f2e;border:1px solid #2a2f3e;'
                f'border-top:2px solid {color};border-radius:6px;padding:14px">'
                f'<p style="margin:0 0 1px;font-size:26px;font-weight:700;color:{color}">{count}</p>'
                f'<p style="margin:0 0 6px;font-size:12px;color:#94a3b8">{label} · {pct}%</p>'
                f'<p style="margin:0;font-size:12px;color:#64748b">Receita: {sub_rev}</p>'
                f'<p style="margin:2px 0 0;font-size:12px;color:#64748b">Ticket médio: {sub_tkt}</p>'
                f'</div></td>'
            )

        retencao_block = (
            f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
            + _ret_card(novos, novos_pct, "#e2e8f0", "Novos", rev_nov, tkt_nov)
            + _ret_card(recorr, recorr_pct, "#10b981", "Recorrentes", rev_rec, tkt_rec).replace("padding-right:6px", "padding-left:6px")
            + f'</tr></table>'
            + (f'<p style="margin:8px 0 0;font-size:12px;color:#64748b">'
               f'Taxa de recorrência: {taxa} — '
               f'clientes que retornaram para uma segunda compra.</p>')
        )

    # ── 11. Destaques + Atenções (IA) ─────────────────────────────────────
    insights_html = ""
    for d in destaques[:5]:
        tipo  = d.get("tipo", "destaque")
        col   = "#10b981" if tipo == "destaque" else "#f59e0b"
        tit   = _h(d.get("titulo"))
        txt   = _h(d.get("descricao") or d.get("texto"), "")
        insights_html += (
            f'<div style="background:#1a1f2e;border-left:3px solid {col};'
            f'border-radius:0 6px 6px 0;padding:10px 14px;margin-bottom:8px">'
            f'<p style="margin:0 0 2px;font-size:10px;color:{col};'
            f'text-transform:uppercase;letter-spacing:0.5px">{tipo}</p>'
            f'<p style="margin:0;font-size:13px;font-weight:600;color:#e2e8f0">{tit}</p>'
            + (f'<p style="margin:4px 0 0;font-size:13px;color:#94a3b8;line-height:1.5">{txt}</p>' if txt else "")
            + "</div>"
        )
    for a in atencoes[:3]:
        tit = _h(a.get("titulo"))
        txt = _h(a.get("descricao") or a.get("texto"), "")
        insights_html += (
            f'<div style="background:#1a1f2e;border-left:3px solid #ef4444;'
            f'border-radius:0 6px 6px 0;padding:10px 14px;margin-bottom:8px">'
            f'<p style="margin:0 0 2px;font-size:10px;color:#ef4444;'
            f'text-transform:uppercase;letter-spacing:0.5px">atenção</p>'
            f'<p style="margin:0;font-size:13px;font-weight:600;color:#e2e8f0">{tit}</p>'
            + (f'<p style="margin:4px 0 0;font-size:13px;color:#94a3b8;line-height:1.5">{txt}</p>' if txt else "")
            + "</div>"
        )

    # ── 12. Plano próximo mês ─────────────────────────────────────────────
    plano_block = ""
    if plano:
        acoes      = plano.get("acoes") or []
        next_mes   = _h(plano.get("mes"), "Próximo mês")
        acoes_html = "".join(
            f'<p style="margin:0 0 7px;font-size:13px;color:#94a3b8;line-height:1.5">'
            f'<span style="color:{cor_primaria};font-weight:700">{i+1}.</span> {acao}</p>'
            for i, acao in enumerate(acoes[:6])
        )

        def _pcard(label, val, color="#e2e8f0"):
            return (
                f'<td width="33%" style="padding-right:6px;vertical-align:top">'
                f'<div style="background:#1a1f2e;border:1px solid #2a2f3e;border-radius:8px;padding:12px">'
                f'<p style="margin:0 0 2px;font-size:10px;color:#64748b;text-transform:uppercase">{label}</p>'
                f'<p style="margin:0;font-size:17px;font-weight:700;color:{color}">{val}</p>'
                f'</div></td>'
            )

        plano_block = (
            f'<p style="margin:0 0 12px;font-size:13px;color:#64748b">Objetivos para {next_mes}</p>'
            f'<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:16px"><tr>'
            + _pcard("Meta Faturamento", plano_fat_fmt, "#10b981")
            + _pcard("Meta ROAS", plano_roas_fmt + ("x" if plano_roas_fmt not in ("—","") and not plano_roas_fmt.endswith("x") else ""))
            + _pcard("Budget Total", plano_bud_fmt)
            + f'</tr></table>'
            + acoes_html
        )

    # ── Render ────────────────────────────────────────────────────────────
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
<tr><td align="center" style="padding:24px 8px 40px">

<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%">

  <!-- HEADER -->
  <tr>
    <td style="background:#1a1f2e;border-radius:12px 12px 0 0;padding:24px 28px;
               border-bottom:1px solid #2a2f3e">
      <table width="100%" cellpadding="0" cellspacing="0"><tr>
        <td>
          <p style="margin:0;font-size:12px;color:{cor_primaria};font-weight:600;
             text-transform:uppercase;letter-spacing:0.8px">{nome_agencia}</p>
          <p style="margin:4px 0 0;font-size:22px;font-weight:700;color:#f1f5f9">{nome_cliente}</p>
        </td>
        <td align="right" style="vertical-align:top">
          <div style="background:#0c0e14;border:1px solid #2a2f3e;border-radius:8px;
                      padding:8px 14px;display:inline-block">
            <p style="margin:0;font-size:10px;color:#64748b">Relatório Mensal</p>
            <p style="margin:2px 0 0;font-size:15px;font-weight:700;color:#e2e8f0">{mes_label}</p>
          </div>
        </td>
      </tr></table>
    </td>
  </tr>

  <!-- SCORECARD -->
  <tr>
    <td style="background:{sc_color}18;border-left:4px solid {sc_color};padding:14px 28px">
      <p style="margin:0 0 2px;font-size:19px">{sc_emoji}
        <span style="font-size:15px;font-weight:700;color:{sc_color}">{sc_label}</span></p>
      {f'<p style="margin:0;font-size:13px;color:#94a3b8">{sc_verdict}</p>' if sc_verdict else ''}
    </td>
  </tr>

  <!-- BODY -->
  <tr><td style="background:#141720;padding:28px 28px 8px;border-radius:0 0 12px 12px">
    <table width="100%" cellpadding="0" cellspacing="0">

      <!-- 3. Resumo executivo -->
      {_section("Resumo Executivo",
          f'<p style="margin:0;font-size:13px;color:#94a3b8;line-height:1.65">{resumo_exec}</p>'
      ) if resumo_exec else ""}

      <!-- 4. KPIs -->
      {_section("KPIs do Mês",
          f'<table width="100%" cellpadding="0" cellspacing="0">{kpi_rows_html}</table>'
      )}

      <!-- 5. Comparativo Anual -->
      {_section(f"Comparativo vs Ano Anterior ({ano_ant_label})", comp_block) if comp_block else ""}

      <!-- 6. Metas -->
      {_section("Metas vs Realizado",
          f'<table width="100%" cellpadding="0" cellspacing="0">{metas_rows}</table>'
      ) if metas_rows else ""}

      <!-- 7. Canais (atribuição server-side) -->
      {_section("Desempenho por Canal",
          canal_block,
          note="Receita e ROAS por atribuição server-side (nossa tracking)."
      ) if canal_block else ""}

      <!-- 8. Análise AI dos canais -->
      {_section("Análise dos Canais",
          f'<p style="margin:0;font-size:13px;color:#94a3b8;line-height:1.65">{analise_canais}</p>'
      ) if analise_canais else ""}

      <!-- 9–10. Campanhas por canal (platform-reported) -->
      {_section("Principais Campanhas",
          camp_blocks,
          note="Dados reportados pelas plataformas (Meta/Google). ROAS pode divergir da atribuição server-side."
      ) if camp_blocks else ""}

      <!-- 11. Atribuição por canal -->
      {_section("Atribuição de Receita por Canal",
          f'<table width="100%" cellpadding="0" cellspacing="0">{atr_rows}</table>'
          + (f'<p style="margin:10px 0 0;font-size:12px;color:#475569">'
             f'Cobertura paga: {coverage_pct:.0f}% da receita atribuída a canais pagos (utm_source). '
             f'ROAS Geral ({_h(context.get("roas_fmt"),"—")}) = MER total; ROAS por canal = '
             f'receita atribuída ÷ investimento do canal.</p>' if atr_rows else "")
      ) if atr_rows else ""}

      <!-- 12. Funil de conversão -->
      {_section("Funil de Conversão",
          f'<table width="100%" cellpadding="0" cellspacing="0">{funil_rows}</table>'
      ) if funil_rows else ""}

      <!-- 13. Eficiência de mídia -->
      {_section("Eficiência de Mídia", ef_block) if ef_block else ""}

      <!-- 14. Top produtos -->
      {_section("Top Produtos",
          f'<table width="100%" cellpadding="0" cellspacing="0">{produto_rows}</table>'
      ) if produto_rows else ""}

      <!-- 15. Retenção -->
      {_section("Retenção de Clientes", retencao_block) if retencao_block else ""}

      <!-- 16. Destaques & Atenções -->
      {_section("Destaques &amp; Atenções", insights_html) if insights_html else ""}

      <!-- 17. Plano -->
      {_section("Plano para o Próximo Mês", plano_block) if plano_block else ""}

      <!-- Footer -->
      <tr><td style="padding:8px 0 24px;border-top:1px solid #2a2f3e">
        <p style="margin:0;font-size:12px;color:#475569">
          {nome_agencia}{f' · <a href="{site_agencia}" style="color:{cor_primaria};text-decoration:none">{site_agencia}</a>' if site_agencia else ""}
        </p>
        <p style="margin:4px 0 0;font-size:11px;color:#374151">
          Relatório gerado automaticamente · {nome_cliente} · {mes_label}
        </p>
      </td></tr>

    </table>
  </td></tr>

</table>

</td></tr>
</table>
</body>
</html>"""
