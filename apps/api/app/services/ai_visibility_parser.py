"""
Parser de CSV do Ubersuggest AI Search Visibility.

IMPORTANTE: Os nomes de coluna exactos precisam ser validados com um CSV real
exportado do Ubersuggest antes do primeiro import de produção. O mapping em
COLUMN_ALIASES abaixo cobre variações esperadas e deve ser ajustado se necessário.

Suporta dois tipos de CSV:
  - 'prompts'     — métrica por prompt (uma linha por prompt × plataforma × data)
  - 'competitors' — menções de competidores (uma linha por menção)

Fluxo:
  1. UbersuggestCSVParser(client_id, bytes).validate() → ValidationResult
  2. Se ok: .import_to_db(import_id) → ImportResult
  3. Caso contrário: mostrar erros ao usuário e abortar
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from ..database import get_supabase

logger = logging.getLogger(__name__)

# ── Mapeamento de colunas ─────────────────────────────────────────────────────
# Cada chave é o nome interno; o valor é uma lista de nomes possíveis no CSV
# (case-insensitive). Atualizar quando o formato real do Ubersuggest for validado.

COLUMN_ALIASES: dict[str, list[str]] = {
    # ── CSV de prompts ────────────────────────────────────────────────────────
    "prompt_id":        ["prompt_id", "id", "promptid"],
    "prompt_text":      ["prompt_text", "prompt", "query", "question"],
    "platform":         ["platform", "ai_platform", "source", "engine"],
    "date":             ["date", "checked_date", "data", "period"],
    "brand_mentioned":  ["brand_mentioned", "mentioned", "is_mentioned", "brand_found"],
    "brand_position":   ["brand_position", "position", "rank", "brand_rank"],
    "sentiment":        ["sentiment", "brand_sentiment", "tone"],
    "response_snippet": ["response_snippet", "snippet", "context", "excerpt", "response_text"],
    "total_brands":     ["total_brands", "total_brands_mentioned", "brands_count"],
    # ── CSV de competidores ───────────────────────────────────────────────────
    "competitor_brand": ["competitor_brand", "competitor", "brand", "brand_name"],
    "competitor_pos":   ["competitor_position", "competitor_pos", "comp_position"],
    "competitor_sent":  ["competitor_sentiment", "comp_sentiment"],
}

# Plataformas conhecidas — normaliza variações de nome
_PLATFORM_MAP = {
    "chatgpt":    "chatgpt",
    "chat gpt":   "chatgpt",
    "gpt":        "chatgpt",
    "openai":     "chatgpt",
    "gemini":     "gemini",
    "google":     "gemini",
    "bard":       "gemini",
    "perplexity": "perplexity",
    "claude":     "claude",
    "anthropic":  "claude",
    "copilot":    "copilot",
    "bing":       "copilot",
}

_BOOL_TRUE  = {"true", "yes", "1", "sim", "s", "t"}
_BOOL_FALSE = {"false", "no", "0", "não", "nao", "n", "f"}


@dataclass
class ValidationResult:
    valid:         bool
    csv_type:      Optional[str]
    total_rows:    int
    errors:        list[str]  = field(default_factory=list)
    warnings:      list[str]  = field(default_factory=list)
    period_start:  Optional[str] = None
    period_end:    Optional[str] = None
    platforms:     list[str]  = field(default_factory=list)
    sample_rows:   list[dict] = field(default_factory=list)
    col_map:       dict       = field(default_factory=dict)   # coluna interna → nome real no CSV


@dataclass
class ImportResult:
    rows_processed: int
    rows_skipped:   int
    errors:         list[dict]


class UbersuggestCSVParser:
    """Parser idempotente para CSV do Ubersuggest AI Search Visibility."""

    def __init__(self, client_id: str, file_bytes: bytes):
        self.client_id = client_id
        self._raw      = file_bytes

    # ── helpers ────────────────────────────────────────────────────────────────

    def _read_csv(self) -> tuple[list[str], list[dict]]:
        text   = self._raw.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        rows   = list(reader)
        return list(reader.fieldnames or []), rows

    @staticmethod
    def _resolve_col(headers: list[str], aliases: list[str]) -> Optional[str]:
        """Retorna o primeiro header real que corresponde a um alias (case-insensitive)."""
        lower_headers = {h.lower().strip(): h for h in headers}
        for alias in aliases:
            if alias.lower() in lower_headers:
                return lower_headers[alias.lower()]
        return None

    def _build_col_map(self, headers: list[str]) -> dict[str, Optional[str]]:
        return {
            internal: self._resolve_col(headers, aliases)
            for internal, aliases in COLUMN_ALIASES.items()
        }

    @staticmethod
    def _detect_csv_type(col_map: dict[str, Optional[str]]) -> Optional[str]:
        """Detecta tipo de CSV pelas colunas obrigatórias presentes."""
        if col_map.get("prompt_text") and col_map.get("brand_mentioned"):
            return "prompts"
        if col_map.get("competitor_brand") and col_map.get("platform"):
            return "competitors"
        return None

    @staticmethod
    def _normalize_platform(raw: str) -> str:
        return _PLATFORM_MAP.get(raw.lower().strip(), raw.lower().strip())

    @staticmethod
    def _parse_bool(val: str) -> Optional[bool]:
        v = val.strip().lower()
        if v in _BOOL_TRUE:
            return True
        if v in _BOOL_FALSE:
            return False
        return None

    @staticmethod
    def _parse_date(val: str) -> Optional[str]:
        """Aceita YYYY-MM-DD, DD/MM/YYYY, MM/DD/YYYY."""
        val = val.strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(val, fmt).date().isoformat()
            except ValueError:
                continue
        return None

    # ── public API ─────────────────────────────────────────────────────────────

    def validate(self) -> ValidationResult:
        try:
            headers, rows = self._read_csv()
        except Exception as exc:
            return ValidationResult(valid=False, csv_type=None, total_rows=0, errors=[f"Erro ao ler CSV: {exc}"])

        if not rows:
            return ValidationResult(valid=False, csv_type=None, total_rows=0, errors=["CSV vazio"])

        col_map  = self._build_col_map(headers)
        csv_type = self._detect_csv_type(col_map)
        result   = ValidationResult(valid=True, csv_type=csv_type, total_rows=len(rows), col_map=col_map)

        if not csv_type:
            result.errors.append(
                f"Tipo de CSV não reconhecido. Colunas encontradas: {', '.join(headers[:10])}. "
                "Verifique se está exportando a aba correta do Ubersuggest."
            )
            result.valid = False
            return result

        # Colunas obrigatórias por tipo
        required = {
            "prompts":     ["prompt_text", "platform", "date", "brand_mentioned"],
            "competitors": ["competitor_brand", "platform", "date"],
        }[csv_type]

        for col in required:
            if not col_map.get(col):
                result.errors.append(f"Coluna obrigatória não encontrada: '{col}' (esperava algo como: {COLUMN_ALIASES[col][:2]})")
                result.valid = False

        if not result.valid:
            return result

        # Validar datas e plataformas
        dates, platforms, bad_rows = [], set(), 0
        date_col     = col_map.get("date")
        platform_col = col_map.get("platform")

        for i, row in enumerate(rows[:500], start=2):  # valida até 500 linhas
            d = self._parse_date(row.get(date_col, "")) if date_col else None
            if not d:
                bad_rows += 1
                if bad_rows <= 3:
                    result.warnings.append(f"Linha {i}: data inválida ('{row.get(date_col, '')}')")
                continue
            dates.append(d)
            if platform_col:
                platforms.add(self._normalize_platform(row.get(platform_col, "desconhecido")))

        if dates:
            result.period_start = min(dates)
            result.period_end   = max(dates)
        if bad_rows > 3:
            result.warnings.append(f"...e mais {bad_rows - 3} linhas com data inválida (serão ignoradas)")

        result.platforms   = sorted(platforms)
        result.sample_rows = [dict(r) for r in rows[:10]]

        if not dates:
            result.errors.append("Nenhuma data válida encontrada no CSV")
            result.valid = False

        return result

    def import_to_db(self, import_id: str) -> ImportResult:
        """Importa dados validados para o banco. Idempotente — upsert por (client_id, prompt_id, date, platform)."""
        _, rows = self._read_csv()
        headers  = list(rows[0].keys()) if rows else []
        col_map  = self._build_col_map(headers)
        csv_type = self._detect_csv_type(col_map)

        if csv_type == "prompts":
            return self._import_prompts(rows, col_map, import_id)
        if csv_type == "competitors":
            return self._import_competitors(rows, col_map, import_id)
        return ImportResult(rows_processed=0, rows_skipped=0, errors=[{"error": "tipo de CSV não reconhecido"}])

    def _import_prompts(self, rows: list[dict], col: dict, import_id: str) -> ImportResult:
        sb = get_supabase()
        processed, skipped, errors = 0, 0, []

        prompt_cache: dict[str, str] = {}  # external_prompt_id → internal UUID

        for i, row in enumerate(rows, start=2):
            try:
                date_str  = self._parse_date(row.get(col["date"], "")) if col.get("date") else None
                platform  = self._normalize_platform(row.get(col["platform"], "")) if col.get("platform") else None
                prompt_text = str(row.get(col["prompt_text"], "")).strip() if col.get("prompt_text") else ""
                ext_pid   = str(row.get(col.get("prompt_id", ""), "")).strip() or None

                if not (date_str and platform and prompt_text):
                    skipped += 1
                    continue

                # Resolver ou criar prompt
                cache_key = ext_pid or prompt_text
                if cache_key not in prompt_cache:
                    prompt_cache[cache_key] = self._resolve_prompt(sb, prompt_text, ext_pid)

                prompt_id = prompt_cache[cache_key]

                mentioned = None
                if col.get("brand_mentioned"):
                    mentioned = self._parse_bool(row.get(col["brand_mentioned"], ""))

                position = None
                if col.get("brand_position"):
                    try:
                        position = int(float(row.get(col["brand_position"], "")))
                    except (ValueError, TypeError):
                        pass

                sentiment = None
                if col.get("sentiment"):
                    s = row.get(col["sentiment"], "").lower().strip()
                    if s in ("positive", "positivo"):
                        sentiment = "positive"
                    elif s in ("negative", "negativo"):
                        sentiment = "negative"
                    elif s in ("neutral", "neutro"):
                        sentiment = "neutral"

                context = None
                if col.get("response_snippet"):
                    context = str(row.get(col["response_snippet"], "")).strip()[:1000] or None

                total_brands = None
                if col.get("total_brands"):
                    try:
                        total_brands = int(float(row.get(col["total_brands"], "")))
                    except (ValueError, TypeError):
                        pass

                sb.table("ai_visibility_metrics").upsert({
                    "client_id":             self.client_id,
                    "prompt_id":             prompt_id,
                    "date":                  date_str,
                    "platform":              platform,
                    "own_brand_mentioned":   mentioned if mentioned is not None else False,
                    "own_brand_position":    position,
                    "own_brand_sentiment":   sentiment,
                    "own_brand_context":     context,
                    "total_brands_mentioned": total_brands,
                    "import_id":             import_id,
                }, on_conflict="client_id,prompt_id,date,platform").execute()

                processed += 1

            except Exception as exc:
                logger.warning("ai_visibility_parser: row %d failed: %s", i, exc)
                errors.append({"row": i, "error": str(exc)[:200]})
                skipped += 1

        return ImportResult(rows_processed=processed, rows_skipped=skipped, errors=errors)

    def _import_competitors(self, rows: list[dict], col: dict, import_id: str) -> ImportResult:
        sb = get_supabase()
        processed, skipped, errors = 0, 0, []

        for i, row in enumerate(rows, start=2):
            try:
                date_str   = self._parse_date(row.get(col["date"], "")) if col.get("date") else None
                platform   = self._normalize_platform(row.get(col["platform"], "")) if col.get("platform") else None
                brand_name = str(row.get(col["competitor_brand"], "")).strip() if col.get("competitor_brand") else ""

                if not (date_str and platform and brand_name):
                    skipped += 1
                    continue

                position = None
                if col.get("competitor_pos"):
                    try:
                        position = int(float(row.get(col["competitor_pos"], "")))
                    except (ValueError, TypeError):
                        pass

                sentiment = None
                if col.get("competitor_sent"):
                    s = row.get(col["competitor_sent"], "").lower().strip()
                    if s in ("positive", "positivo"):
                        sentiment = "positive"
                    elif s in ("negative", "negativo"):
                        sentiment = "negative"
                    elif s in ("neutral", "neutro"):
                        sentiment = "neutral"

                # Buscar brand_id se cadastrado
                brand_row = (
                    sb.table("ai_visibility_brands")
                    .select("id")
                    .eq("client_id", self.client_id)
                    .eq("brand_name", brand_name)
                    .limit(1)
                    .execute()
                ).data
                brand_id = brand_row[0]["id"] if brand_row else None

                # Encontrar metric_id correspondente para o mesmo prompt/data/platform
                # (sem prompt_id no CSV de competidores, inserimos sem referência)
                sb.table("ai_visibility_competitor_mentions").insert({
                    "client_id": self.client_id,
                    "metric_id": None,   # será linkado manualmente ou via recálculo futuro
                    "brand_id":  brand_id,
                    "brand_name": brand_name,
                    "position":  position,
                    "sentiment": sentiment,
                    "date":      date_str,
                    "platform":  platform,
                }).execute()

                processed += 1

            except Exception as exc:
                logger.warning("ai_visibility_parser competitors: row %d failed: %s", i, exc)
                errors.append({"row": i, "error": str(exc)[:200]})
                skipped += 1

        return ImportResult(rows_processed=processed, rows_skipped=skipped, errors=errors)

    def _resolve_prompt(self, sb, prompt_text: str, ext_id: Optional[str]) -> str:
        """Retorna UUID do prompt existente ou cria um novo."""
        q = sb.table("ai_visibility_prompts").select("id").eq("client_id", self.client_id)
        if ext_id:
            existing = q.eq("external_prompt_id", ext_id).limit(1).execute().data
        else:
            existing = q.eq("prompt_text", prompt_text).limit(1).execute().data

        if existing:
            return existing[0]["id"]

        new_row = (
            sb.table("ai_visibility_prompts").insert({
                "client_id":        self.client_id,
                "prompt_text":      prompt_text,
                "external_prompt_id": ext_id,
            }).execute()
        ).data[0]
        return new_row["id"]
