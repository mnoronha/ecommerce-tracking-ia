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
