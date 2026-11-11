
from pathlib import Path
from datetime import datetime, timezone
import json
import tempfile
import argparse

import requests

try:
    from snapshots import proteger_area_operacional, ler_manifesto, restaurar_arquivo
    from integridade_ingestao import validar_csv_aneel, cache_valido, publicar, sha256
    from rede import repetir_transitorios
except ModuleNotFoundError:
    from src.snapshots import proteger_area_operacional, ler_manifesto, restaurar_arquivo
    from src.integridade_ingestao import validar_csv_aneel, cache_valido, publicar, sha256
    from src.rede import repetir_transitorios

# =========================================
# CONFIGURAÇÕES
# =========================================

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw" / "aneel"
STATE_FILE = BASE_DIR / "data" / "controle_aneel.json"

API_URL = (
    "https://dadosabertos.aneel.gov.br/api/3/"
    "action/package_show"
)

DATASET = "siga-sistema-de-informacoes-de-geracao-da-aneel"

ARQUIVO = RAW_DIR / "siga_empreendimentos_diario.csv"


# =========================================
# CONSULTAR CATÁLOGO
# =========================================

def consultar_recurso():
    resposta = requests.get(
        API_URL,
        params={"id": DATASET},
        timeout=30
    )
    resposta.raise_for_status()

    dados = resposta.json()

    if not dados.get("success"):
        raise RuntimeError("Erro no catálogo da ANEEL")

    recursos = dados["result"]["resources"]

    for recurso in recursos:
        nome = recurso.get("name", "").lower()
        formato = str(recurso.get("format") or "").upper()

        if (
            "siga-empreendimentos-geracao-diario.csv" in nome
            and formato == "CSV"
        ):
            return recurso

    raise RuntimeError("CSV diário não encontrado")


# =========================================
# CONTROLE DE VERSÃO
# =========================================

def carregar_estado():
    if STATE_FILE.exists():
        return json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
    return {}


def salvar_estado(estado):
    temp = STATE_FILE.with_suffix(".tmp")

    temp.write_text(
        json.dumps(estado, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    temp.replace(STATE_FILE)


# =========================================
# EXTRAÇÃO
# =========================================

@repetir_transitorios
def atualizar():
    proteger_area_operacional(RAW_DIR)
    print("=== EXTRAÇÃO INCREMENTAL ANEEL ===")

    recurso = consultar_recurso()
    estado = carregar_estado()

    url = recurso["url"]
    versao = recurso.get("hash")
    data_modificacao = (
        recurso.get("last_modified")
        or recurso.get("metadata_modified")
    )

    valido = estado.get("url") == url and cache_valido(ARQUIVO, estado, validar_csv_aneel)
    if valido and versao and estado.get("hash") == versao:
        print("Arquivo já atualizado. Download ignorado.")
        return
    print("Nova versão detectada. Baixando...")
    headers = {}
    if valido and estado.get("etag"):
        headers["If-None-Match"] = estado["etag"]
    if valido and estado.get("http_last_modified"):
        headers["If-Modified-Since"] = estado["http_last_modified"]
    temp_path = None
    try:
        for tentativa in range(2):
            with requests.get(url, headers=headers, stream=True, timeout=(15, 120)) as resposta:
                if resposta.status_code == 304:
                    if headers and cache_valido(ARQUIVO, estado, validar_csv_aneel):
                        print("Sem alteração; cache verificado.")
                        return
                    if tentativa == 0:
                        headers = {}
                        continue
                    raise ValueError("HTTP 304 sem cache íntegro; recuperação incondicional falhou.")
                resposta.raise_for_status()
                if resposta.status_code != 200:
                    raise ValueError(f"HTTP inesperado: {resposta.status_code}")
                etag = resposta.headers.get("ETag")
                http_last_modified = resposta.headers.get("Last-Modified")
                with tempfile.NamedTemporaryFile(dir=RAW_DIR, suffix=".csv", delete=False) as temp:
                    temp_path = Path(temp.name)
                    for bloco in resposta.iter_content(chunk_size=1024 * 1024):
                        if bloco:
                            temp.write(bloco)
            break
        validar_csv_aneel(temp_path)
        novo_hash = sha256(temp_path)
        igual = valido and sha256(ARQUIVO) == novo_hash
        novo_estado = {
            **estado,
            "url": url,
            "hash": versao,
            "last_modified": data_modificacao,
            "etag": etag,
            "http_last_modified": http_last_modified,
            "baixado_em": estado.get("baixado_em") if igual else datetime.now(timezone.utc).isoformat(),
            "tamanho_bytes": temp_path.stat().st_size,
            "sha256": novo_hash,
        }
        if igual:
            salvar_estado(novo_estado)
        else:
            publicar(temp_path, ARQUIVO, lambda: salvar_estado(novo_estado), STATE_FILE)
        print(f"Download concluído: {ARQUIVO.name}")
        print(
            f"Tamanho: {ARQUIVO.stat().st_size / 1024**2:.2f} MB"
        )

    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main():
    global RAW_DIR, STATE_FILE, ARQUIVO
    parser = argparse.ArgumentParser(description="ANEEL: atualizar CSV operacional ou reutilizar uma edição local.")
    modo = parser.add_mutually_exclusive_group(required=True)
    modo.add_argument("--atualizar", action="store_true")
    modo.add_argument("--snapshot", help="Data/identificador da edição local; nunca consulta a rede.")
    parser.add_argument("--manifestos", type=Path, default=BASE_DIR / "edicoes")
    parser.add_argument("--armazenamento", type=Path, default=BASE_DIR / "data/snapshots")
    parser.add_argument("--data-dir", type=Path, default=BASE_DIR / "data")
    args = parser.parse_args()
    pasta = args.data_dir.resolve()
    proteger_area_operacional(pasta)
    RAW_DIR = pasta / "raw/aneel"
    STATE_FILE = pasta / "controle_aneel.json"
    ARQUIVO = RAW_DIR / "siga_empreendimentos_diario.csv"
    proteger_area_operacional(RAW_DIR)
    if args.snapshot:
        import re
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", args.snapshot):
            parser.error("Identificador de snapshot inválido.")
        manifesto = ler_manifesto(args.manifestos / f"{args.snapshot}.json")
        if manifesto["edicao"] != args.snapshot:
            raise ValueError("Identificador do manifesto difere do snapshot solicitado.")
        restaurar_arquivo(manifesto, args.armazenamento,
                          "data/raw/aneel/siga_empreendimentos_diario.csv", ARQUIVO)
        print(f"Snapshot ANEEL verificado: {args.snapshot}")
    else:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        atualizar()


if __name__ == "__main__":
    main()
