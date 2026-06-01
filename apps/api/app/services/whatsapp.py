"""
WhatsApp delivery via Evolution API (self-hosted).

Configure via env vars:
  EVOLUTION_API_URL      — URL base da sua instância Evolution (sem trailing slash)
                           ex: https://evolution.suaempresa.com
  EVOLUTION_API_KEY      — API key global do Evolution
  EVOLUTION_INSTANCE     — nome da instância WhatsApp (ex: noroia-principal)
  AGENCY_WHATSAPP        — número da agência (ex: 5511999999999)
  EVOLUTION_MIN_SEVERITY — mínima severidade para disparar WA: critical | warning | all

Endpoint Evolution usado:
  POST /message/sendText/{instance}
  Headers: apikey: {api_key}
  Body: { "number": "<phone>@s.whatsapp.net", "text": "<msg>" }
"""

from __future__ import annotations

import logging
import re

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_MAX_LEN = 4000  # WhatsApp permite até ~65k chars, mas mensagens longas são ruins


def _normalize_phone(phone: str) -> str:
    """
    Remove tudo que não seja dígito e garante formato DDI+DDD+Número.
    Adiciona 55 (Brasil) se não houver DDI.
    """
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return ""
    # Brasil: 55 + 2 DDD + 8/9 dígitos = 12 ou 13 chars
    if len(digits) <= 11:
        digits = "55" + digits
    return digits


def _is_configured() -> bool:
    return bool(
        settings.EVOLUTION_API_URL
        and settings.EVOLUTION_API_KEY
        and settings.EVOLUTION_INSTANCE
    )


def send_text(phone: str, text: str) -> bool:
    """
    Envia uma mensagem de texto simples via Evolution API.
    Retorna True se enviado com sucesso, False caso contrário.
    """
    if not _is_configured():
        logger.debug("whatsapp: Evolution API não configurada — skip send para %s", phone)
        return False

    phone = _normalize_phone(phone)
    if not phone:
        logger.warning("whatsapp: número inválido fornecido")
        return False

    text = text[:_MAX_LEN]
    url  = f"{settings.EVOLUTION_API_URL.rstrip('/')}/message/sendText/{settings.EVOLUTION_INSTANCE}"

    try:
        resp = httpx.post(
            url,
            headers={
                "apikey":       settings.EVOLUTION_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "number": f"{phone}@s.whatsapp.net",
                "text":   text,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        logger.info("whatsapp: enviado → %s", phone)
        return True
    except httpx.HTTPStatusError as exc:
        logger.error("whatsapp HTTP %s → %s: %s",
                     exc.response.status_code, phone, exc.response.text[:300])
        return False
    except Exception as exc:
        logger.error("whatsapp error → %s: %s", phone, exc)
        return False


def send_to_agency(text: str) -> bool:
    """Atalho para enviar para o AGENCY_WHATSAPP configurado."""
    phone = settings.AGENCY_WHATSAPP
    if not phone:
        logger.debug("whatsapp: AGENCY_WHATSAPP não configurado — skip")
        return False
    return send_text(phone, text)


def severity_should_notify(severity: str) -> bool:
    """
    Retorna True se a severidade deve disparar notificação WhatsApp,
    com base em EVOLUTION_MIN_SEVERITY.
    """
    min_sev = (settings.EVOLUTION_MIN_SEVERITY or "critical").lower()
    sev     = severity.lower()
    if min_sev == "all":
        return True
    if min_sev == "warning":
        return sev in ("critical", "warning")
    return sev == "critical"  # default: só crítico


def check_instance_status() -> dict:
    """
    Verifica se a instância Evolution está conectada.
    Retorna dict com status, state e connection_state.
    """
    if not _is_configured():
        return {"ok": False, "error": "Evolution API não configurada"}

    url = f"{settings.EVOLUTION_API_URL.rstrip('/')}/instance/fetchInstances"
    try:
        resp = httpx.get(
            url,
            headers={"apikey": settings.EVOLUTION_API_KEY},
            timeout=10.0,
        )
        resp.raise_for_status()
        data     = resp.json()
        # Evolution retorna lista de instâncias
        instances = data if isinstance(data, list) else data.get("instances", [])
        for inst in instances:
            name = inst.get("instance", {}).get("instanceName") or inst.get("instanceName", "")
            if name == settings.EVOLUTION_INSTANCE:
                state = (
                    inst.get("instance", {}).get("connectionStatus")
                    or inst.get("connectionStatus", "unknown")
                )
                return {
                    "ok":    state.lower() == "open",
                    "state": state,
                    "instance": settings.EVOLUTION_INSTANCE,
                }
        return {"ok": False, "error": f"Instância '{settings.EVOLUTION_INSTANCE}' não encontrada"}
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
