"""
Encriptação de credenciais em repouso (Fernet, chave FORA do banco).

A chave vive em `CREDENTIALS_KEY` (env do Railway). Valores cifrados levam o
prefixo `enc:v1:` — assim sabemos, sem ambiguidade, se um valor está cifrado.

Design seguro pra rollout sem downtime:
- `decrypt_secret` faz fallback transparente: se o valor não tem o prefixo,
  devolve como está (texto puro legado). Então dá pra ligar a leitura via
  decrypt em todo lugar ANTES de migrar os dados.
- `encrypt_secret` é idempotente e no-op se não houver chave — nunca quebra.

Só migrar (cifrar os valores existentes) DEPOIS que todos os pontos de leitura
estiverem passando por `decrypt_secret`.
"""

import logging
from typing import Optional

from ..config import settings

logger = logging.getLogger(__name__)

_PREFIX = "enc:v1:"
_fernet = None
_init = False


def _get_fernet():
    global _fernet, _init
    if _init:
        return _fernet
    _init = True
    key = getattr(settings, "CREDENTIALS_KEY", None)
    if not key:
        logger.warning("crypto: CREDENTIALS_KEY não definida — credenciais ficam em texto puro")
        return None
    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:
        logger.error("crypto: CREDENTIALS_KEY inválida: %s", exc)
        _fernet = None
    return _fernet


def is_encrypted(value: Optional[str]) -> bool:
    return bool(value) and isinstance(value, str) and value.startswith(_PREFIX)


def encrypt_secret(value: Optional[str]) -> Optional[str]:
    """Cifra um segredo. No-op se vazio, já cifrado, ou sem chave."""
    if not value or is_encrypted(value):
        return value
    f = _get_fernet()
    if not f:
        return value
    try:
        return _PREFIX + f.encrypt(value.encode()).decode()
    except Exception as exc:
        logger.error("crypto: encrypt falhou: %s", exc)
        return value


def decrypt_secret(value: Optional[str]) -> Optional[str]:
    """Decifra. Texto puro (legado) ou vazio passa direto."""
    if not is_encrypted(value):
        return value
    f = _get_fernet()
    if not f:
        logger.error("crypto: valor cifrado mas sem CREDENTIALS_KEY — devolvendo cru")
        return value
    try:
        return f.decrypt(value[len(_PREFIX):].encode()).decode()
    except Exception as exc:
        logger.error("crypto: decrypt falhou: %s", exc)
        return value


# Campos de segredo na tabela `clients` (acesso a contas de ads/loja/webhook).
SECRET_FIELDS = (
    "meta_access_token", "google_ads_refresh_token", "shopify_access_token",
    "tiktok_access_token", "pinterest_access_token", "nuvemshop_access_token",
    "ga4_api_secret", "shopify_webhook_secret", "webhook_secret",
    "woo_consumer_key", "woo_consumer_secret", "woo_webhook_secret",
    "tracking_cname_secret",
)


def decrypt_client_secrets(row: Optional[dict]) -> Optional[dict]:
    """Decifra (in place) os campos de segredo de uma linha de `clients`.
    Chamar logo após buscar a linha — o plaintext flui pra todo uso downstream.
    Idempotente e seguro: valor em texto puro/None passa direto."""
    if not row:
        return row
    for fld in SECRET_FIELDS:
        v = row.get(fld)
        if v:
            row[fld] = decrypt_secret(v)
    return row


def encrypt_client_secrets(data: Optional[dict]) -> Optional[dict]:
    """Cifra (in place) os campos de segredo presentes num dict, antes de gravar."""
    if not data:
        return data
    for fld in SECRET_FIELDS:
        if data.get(fld):
            data[fld] = encrypt_secret(data[fld])
    return data


def encrypt_existing_credentials() -> dict:
    """Migração única: cifra os segredos em texto puro já existentes na tabela
    `clients`. Idempotente — pula valores já cifrados. Usa a chave do ambiente
    (roda no servidor, onde CREDENTIALS_KEY existe)."""
    from ..database import get_supabase
    if not _get_fernet():
        return {"error": "CREDENTIALS_KEY não configurada no ambiente"}
    sb = get_supabase()
    cols = ", ".join(("id",) + SECRET_FIELDS)
    rows = (sb.table("clients").select(cols).execute().data) or []
    clients_updated = 0
    fields_encrypted = 0
    for r in rows:
        patch = {}
        for fld in SECRET_FIELDS:
            v = r.get(fld)
            if v and not is_encrypted(v):
                enc = encrypt_secret(v)
                if is_encrypted(enc):           # só grava se realmente cifrou
                    patch[fld] = enc
        if patch:
            sb.table("clients").update(patch).eq("id", r["id"]).execute()
            clients_updated += 1
            fields_encrypted += len(patch)
    return {"clients_updated": clients_updated, "fields_encrypted": fields_encrypted}
