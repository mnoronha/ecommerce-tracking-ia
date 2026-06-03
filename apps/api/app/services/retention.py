"""
Retenção de dados (LGPD) — apaga eventos brutos de navegação antigos.

`tracking_events` é o dado mais sensível (comportamento por visitante: IP hash,
user-agent, páginas) e o que mais cresce. O valor de negócio já está nos
agregados/pedidos, então mantê-lo indefinidamente é risco legal + custo sem
retorno. Pedidos, ad_spend, atribuições e métricas NÃO são tocados.

Roda diariamente. Apaga em lotes pra não estourar timeout em backlog grande.
"""

import logging
from datetime import datetime, timedelta, timezone

from ..config import settings
from ..database import get_supabase

logger = logging.getLogger(__name__)

# Janela de retenção dos eventos brutos (dias). Configurável por env.
RETENTION_DAYS = int(getattr(settings, "EVENT_RETENTION_DAYS", 0) or 90)


def purge_old_tracking_events(days: int | None = None, batch: int = 5000, max_batches: int = 400) -> dict:
    """Apaga tracking_events com mais de `days` dias, em lotes. Retorna contadores."""
    days = days or RETENTION_DAYS
    sb = get_supabase()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    total = 0
    batches = 0
    try:
        for _ in range(max_batches):
            rows = (
                sb.table("tracking_events")
                .select("id")
                .lt("created_at", cutoff)
                .limit(batch)
                .execute()
            ).data or []
            if not rows:
                break
            ids = [r["id"] for r in rows]
            sb.table("tracking_events").delete().in_("id", ids).execute()
            total += len(ids)
            batches += 1
            if len(rows) < batch:
                break
    except Exception as exc:
        logger.error("retention: purge falhou após %s linhas: %s", total, exc)
        return {"deleted": total, "batches": batches, "cutoff": cutoff, "error": str(exc)[:200]}

    if total:
        logger.info("retention: apagados %s tracking_events anteriores a %s (%s lotes)", total, cutoff, batches)
    return {"deleted": total, "batches": batches, "cutoff": cutoff}


def run_retention() -> None:
    """Entrypoint do scheduler."""
    purge_old_tracking_events()
