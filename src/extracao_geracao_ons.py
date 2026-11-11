
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import argparse
import json
import re
import tempfile

import requests

try:
    from snapshots import proteger_area_operacional
    from integridade_ingestao import validar_parquet, cache_valido, publicar, sha256
    from rede import repetir_transitorios
except ModuleNotFoundError:
    from src.snapshots import proteger_area_operacional
    from src.integridade_ingestao import validar_parquet, cache_valido, publicar, sha256
    from src.rede import repetir_transitorios



# 1. CONFIGURAÇÕES
BASE_DIR = Path(__file__).resolve().parent.parent

RAW_DIR = BASE_DIR / "data/raw/ons/geracao"
STATE_FILE = BASE_DIR / "data/controle_geracao_ons.json"

API_URL = (
    "https://dados.ons.org.br/api/3/action/package_show"
)

DATASET = "geracao-usina-2"

CHUNK_SIZE = 1024 * 1024
TIMEOUT = (15, 180)


# 2. CONTROLE DE ESTADO
def carregar_estado():
    if STATE_FILE.exists():
        return json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
    return {}


def salvar_estado(estado):
    destino_temp = STATE_FILE.with_suffix(".tmp")

    destino_temp.write_text(
        json.dumps(
            estado,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    destino_temp.replace(STATE_FILE)


# 3. DESCOBERTA DINÂMICA
@repetir_transitorios
def consultar_recursos():

    resposta = requests.get(
        API_URL,
        params={"id": DATASET},
        timeout=30
    )

    resposta.raise_for_status()
    dados = resposta.json()

    if not dados.get("success"):
        raise RuntimeError("Falha na API do ONS")

    recursos = []

    for recurso in dados["result"]["resources"]:
        nome = recurso.get("name", "")
        formato = str(
            recurso.get("format") or ""
        ).upper()

        if formato != "PARQUET":
            continue

        match = re.fullmatch(
            r"Geracao_Usina_2-(\d{4})-(\d{2})",
            nome,
            flags=re.IGNORECASE
        )

        if not match:
            continue

        ano, mes = map(int, match.groups())

        if not 1 <= mes <= 12:
            continue

        recursos.append({
            "ano": ano,
            "mes": mes,
            "url": recurso["url"],
            "nome": nome
        })

    recursos.sort(
        key=lambda x: (x["ano"], x["mes"])
    )

    if not recursos:
        raise RuntimeError(
            "Nenhum arquivo Parquet encontrado."
        )

    return recursos


def competencia(valor):
    if not re.fullmatch(r"\d{4}-\d{2}", valor):
        raise argparse.ArgumentTypeError("Use uma competência no formato AAAA-MM.")
    try:
        datetime.strptime(valor, "%Y-%m")
    except ValueError as erro:
        raise argparse.ArgumentTypeError("Competência inválida.") from erro
    return valor


def selecionar_recursos(recursos, inicio=None, fim=None, recentes=None):
    if recentes is not None:
        if recentes < 1 or inicio or fim:
            raise ValueError("Use --recentes positivo OU --inicio e --fim.")
        chaves = [(r["ano"], r["mes"]) for r in recursos]
        if len(chaves) != len(set(chaves)):
            raise ValueError("Recurso mensal ambíguo no catálogo.")
        return sorted(recursos, key=lambda r: (r["ano"], r["mes"]))[-recentes:]
    if not inicio or not fim or competencia(inicio) > competencia(fim):
        raise ValueError("Informe --inicio e --fim em ordem cronológica.")
    esperadas = []
    ano, mes = map(int, inicio.split("-"))
    while f"{ano:04d}-{mes:02d}" <= fim:
        esperadas.append(f"{ano:04d}-{mes:02d}")
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    por_mes = {}
    for recurso in recursos:
        chave = f"{recurso['ano']:04d}-{recurso['mes']:02d}"
        if chave in esperadas:
            if chave in por_mes:
                raise ValueError(f"Recurso mensal ambíguo: {chave}")
            por_mes[chave] = recurso
    faltantes = set(esperadas) - por_mes.keys()
    if faltantes:
        raise ValueError(f"Competências mensais indisponíveis: {sorted(faltantes)}. Não há fallback anual.")
    return [por_mes[chave] for chave in esperadas]


# 4. VERIFICAÇÃO E DOWNLOAD
@repetir_transitorios
def baixar_recurso(recurso, estado):
    proteger_area_operacional(RAW_DIR)
    ano = recurso["ano"]
    mes = recurso["mes"]
    url = recurso["url"]
    chave = f"{ano}-{mes:02d}"
    pasta_ano = RAW_DIR / str(ano)
    proteger_area_operacional(pasta_ano)
    pasta_ano.mkdir(parents=True, exist_ok=True)
    destino = pasta_ano / f"geracao_usina_{ano}_{mes:02d}.parquet"
    info_anterior = estado.get(chave, {})
    validar = lambda arquivo: validar_parquet(arquivo, ano, mes)
    valido = info_anterior.get("url") == url and cache_valido(destino, info_anterior, validar)
    headers = {}
    if valido and info_anterior.get("etag"):
        headers["If-None-Match"] = info_anterior["etag"]
    if valido and info_anterior.get("last_modified"):
        headers["If-Modified-Since"] = info_anterior["last_modified"]
    temporario = None
    print(f"\nVerificando {chave}...")
    try:
        for tentativa in range(2):
            with requests.get(url, headers=headers, stream=True, timeout=TIMEOUT) as resposta:
                if resposta.status_code == 304:
                    if headers and cache_valido(destino, info_anterior, validar):
                        print("Sem alteração; cache verificado.")
                        return "sem_alteracao"
                    if tentativa == 0:
                        headers = {}
                        continue
                    raise ValueError("HTTP 304 sem cache íntegro; recuperação incondicional falhou.")
                resposta.raise_for_status()
                if resposta.status_code != 200:
                    raise ValueError(f"HTTP inesperado: {resposta.status_code}")
                etag = resposta.headers.get("ETag")
                last_modified = resposta.headers.get("Last-Modified")
                with tempfile.NamedTemporaryFile(dir=pasta_ano, suffix=".part", delete=False) as arquivo:
                    temporario = Path(arquivo.name)
                    for bloco in resposta.iter_content(chunk_size=CHUNK_SIZE):
                        if bloco:
                            arquivo.write(bloco)
            break
        validar(temporario)
        novo_hash = sha256(temporario)
        igual = valido and sha256(destino) == novo_hash
        status = "sem_alteracao" if igual else "atualizado"
        agora = datetime.now(ZoneInfo("America/Sao_Paulo")).isoformat()
        novo_estado = {**estado, chave: {
            **info_anterior,
            "url": url, "etag": etag, "last_modified": last_modified,
            "sha256": novo_hash, "tamanho_bytes": temporario.stat().st_size,
            "baixado_em": info_anterior.get("baixado_em") if igual else agora,
            "verificado_em": agora,
        }}
        if igual:
            salvar_estado(novo_estado)
        else:
            publicar(temporario, destino, lambda: salvar_estado(novo_estado), STATE_FILE)
        estado.clear()
        estado.update(novo_estado)
        return status
    finally:
        if temporario is not None:
            temporario.unlink(missing_ok=True)


# 5. EXECUÇÃO PRINCIPAL
def main():

    global RAW_DIR, STATE_FILE
    parser = argparse.ArgumentParser(description="Geração mensal: período explícito ou atualização recente.")
    parser.add_argument("--inicio", type=competencia)
    parser.add_argument("--fim", type=competencia)
    parser.add_argument("--recentes", type=int, help="Atualização operacional dos N recursos mensais mais recentes.")
    parser.add_argument("--data-dir", type=Path, default=BASE_DIR / "data")
    args = parser.parse_args()
    if args.recentes is not None:
        if args.recentes < 1 or args.inicio or args.fim:
            parser.error("Use --recentes positivo OU --inicio e --fim.")
    elif not args.inicio or not args.fim or args.inicio > args.fim:
        parser.error("Selecione --inicio e --fim, ou --recentes N.")
    pasta = args.data_dir.resolve()
    proteger_area_operacional(pasta)
    RAW_DIR = pasta / "raw/ons/geracao"
    STATE_FILE = pasta / "controle_geracao_ons.json"

    print("=== PIPELINE GERAÇÃO ONS ===")

    recursos = selecionar_recursos(consultar_recursos(), args.inicio, args.fim, args.recentes)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    estado = carregar_estado()

    print(f"Arquivos selecionados: {len(recursos)}")

    resultados = {
        "atualizado": 0,
        "sem_alteracao": 0,
        "erro": 0
    }

    for recurso in recursos:
        try:
            status = baixar_recurso(
                recurso,
                estado
            )

            resultados[status] += 1

        except Exception as erro:
            resultados["erro"] += 1

            print(
                f"Erro em {recurso['nome']}: {erro}"
            )

    print("\nResumo:")
    print(resultados)

    if resultados["erro"]:
        raise RuntimeError(
            "Alguns arquivos não foram baixados."
        )

    print("\nPipeline concluído!")


if __name__ == "__main__":
    main()
