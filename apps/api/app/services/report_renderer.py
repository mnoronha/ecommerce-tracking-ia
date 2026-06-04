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

_TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent.parent / "relatorios-agencia"


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
    def _icone(this, tipo):
        return {"meta": "📘", "google": "🔴", "tiktok": "🎵", "pinterest": "📌"}.get(str(tipo), "📡")
    def _seta(this, variacao):
        return {"up": "▲", "down": "▼"}.get(str(variacao), "→")
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
        "icone":  _icone,
        "seta":   _seta,
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
    # Inline Google Fonts as system fonts fallback (WeasyPrint can't load remote fonts)
    css = re.sub(r"@import url\([^)]+\);", "", css)
    css = css.replace("'Sora',", "'Arial',").replace("'Inter',", "sans-serif,")
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
    """Render template → HTML → PDF bytes. Returns None on failure."""
    html = render_monthly_html(context)
    try:
        from weasyprint import HTML as WP_HTML
        return WP_HTML(string=html).write_pdf()
    except Exception as exc:
        logger.error("PDF generation failed: %s", exc)
        return None
