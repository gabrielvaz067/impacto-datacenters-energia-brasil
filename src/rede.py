"""Tentativas limitadas para falhas transitórias das extrações HTTP."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import wraps
import time

import requests

MAX_TENTATIVAS = 3
BACKOFF_INICIAL = 0.5
ESPERA_MAXIMA = 30.0
STATUS_TRANSITORIOS = {429, 500, 502, 503, 504}


def espera_retry_after(valor, agora=None):
    """Retorna segundos solicitados pelo servidor ou None para cabeçalho inválido."""
    if not valor:
        return None
    valor = str(valor).strip()
    if valor.isdigit():
        return float(valor)
    try:
        data = parsedate_to_datetime(valor)
        if data.tzinfo is None:
            return None
        agora = agora or datetime.now(timezone.utc)
        return max(0.0, (data - agora).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def repetir_transitorios(funcao):
    """Repete a operação inteira, inclusive um download interrompido.

    A operação envolvida deve descartar temporários e não publicar dados antes
    de completar a transferência. Erros de certificado TLS não são repetidos.
    """
    @wraps(funcao)
    def executar(*args, **kwargs):
        for tentativa in range(MAX_TENTATIVAS):
            try:
                return funcao(*args, **kwargs)
            except requests.exceptions.RequestException as erro:
                if isinstance(erro, requests.exceptions.SSLError):
                    raise
                resposta = erro.response
                status = resposta.status_code if resposta is not None else None
                recuperavel = (status in STATUS_TRANSITORIOS if isinstance(erro, requests.HTTPError) else
                               isinstance(erro, (requests.ConnectionError, requests.Timeout,
                                                 requests.exceptions.ChunkedEncodingError)))
                if not recuperavel or tentativa + 1 >= MAX_TENTATIVAS:
                    raise
                solicitado = espera_retry_after(resposta.headers.get("Retry-After")) if resposta is not None else None
                if solicitado is not None and solicitado > ESPERA_MAXIMA:
                    # Não repetir antes do prazo solicitado pelo servidor.
                    raise
                espera = max(min(BACKOFF_INICIAL * 2**tentativa, ESPERA_MAXIMA), solicitado or 0)
                if resposta is not None and resposta.raw is not None:
                    resposta.close()
                print(f"Falha HTTP ou de conexão transitória; tentativa {tentativa + 2}/{MAX_TENTATIVAS} em {espera:.1f}s.")
                time.sleep(espera)
        raise AssertionError("Laço de tentativas terminou sem resultado.")
    return executar
