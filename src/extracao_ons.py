"""Extrai a carga diária do ONS e prepara os Parquets anuais de entrada."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlparse

import pandas as pd
import requests

try:
    from snapshots import proteger_area_operacional
    from integridade_ingestao import publicar
    from rede import repetir_transitorios
except ModuleNotFoundError:  # Importação pelos testes a partir da raiz.
    from src.snapshots import proteger_area_operacional
    from src.integridade_ingestao import publicar
    from src.rede import repetir_transitorios


BASE_DIR = Path(__file__).resolve().parent.parent
API_URL = "https://dados.ons.org.br/api/3/action/package_show"
DATASET = "carga-energia"
COLUNAS = [
    "id_subsistema", "nom_subsistema", "din_instante",
    "val_cargaenergiamwmed",
]
SUBSISTEMAS = {"N", "NE", "S", "SE"}
TIMEOUT = (15, 90)


def validar_url(url: str) -> str:
    """Aceita recursos HTTPS nos hosts oficiais usados pelo catálogo ONS."""
    partes = urlparse(url)
    hosts = {
        "dados.ons.org.br",
        "ons-aws-prod-opendata.s3.amazonaws.com",
        "ons-aws-prod-opendata.s3.sa-east-1.amazonaws.com",
    }
    if (partes.scheme != "https" or partes.hostname not in hosts
            or partes.username or partes.password or partes.port not in (None, 443)):
        raise ValueError("URL de recurso fora dos hosts HTTPS esperados do ONS.")
    return url


@repetir_transitorios
def consultar_recursos(sessao: requests.Session, anos: list[int]) -> dict[int, str]:
    with sessao.get(API_URL, params={"id": DATASET}, timeout=TIMEOUT,
                    allow_redirects=False) as resposta:
        resposta.raise_for_status()
        if resposta.status_code != 200:
            raise ValueError("Resposta inesperada do catálogo ONS.")
        dados = resposta.json()
    if not isinstance(dados, dict) or dados.get("success") is not True:
        raise ValueError("Consulta CKAN não retornou success=true.")
    resultado = dados.get("result")
    if not isinstance(resultado, dict) or not isinstance(resultado.get("resources"), list):
        raise ValueError("Catálogo CKAN sem lista de recursos.")

    encontrados = {}
    for recurso in resultado["resources"]:
        if not isinstance(recurso, dict):
            raise ValueError("Entrada inválida na lista de recursos CKAN.")
        nome = str(recurso.get("name") or "").strip()
        match = re.fullmatch(r"Carga_Energia-(\d{4})", nome, re.IGNORECASE)
        if not match or str(recurso.get("format") or "").upper() != "CSV":
            continue
        ano = int(match.group(1))
        if ano not in anos:
            continue
        url = validar_url(recurso["url"])
        if ano in encontrados and encontrados[ano] != url:
            raise ValueError(f"Mais de um recurso CSV para {ano}; revise o catálogo.")
        encontrados[ano] = url
    faltantes = set(anos) - encontrados.keys()
    if faltantes:
        raise ValueError(f"Anos sem CSV anual no catálogo: {sorted(faltantes)}")
    return encontrados


def carregar_estado(caminho: Path) -> dict:
    if not caminho.exists():
        return {}
    try:
        estado = json.loads(caminho.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as erro:
        raise ValueError(f"Controle inválido: {caminho.name}; preserve-o e investigue.") from erro
    if not isinstance(estado, dict) or any(not isinstance(v, dict) for v in estado.values()):
        raise ValueError("Controle deve conter um objeto por ano.")
    return estado


def sha256(caminho: Path) -> str:
    with caminho.open("rb") as arquivo:
        return hashlib.file_digest(arquivo, "sha256").hexdigest()


def ler_carga(caminho: Path, ano: int) -> pd.DataFrame:
    """Valida o contrato diário sem agregar, imputar ou remover duplicatas."""
    df = pd.read_csv(caminho, sep=";", encoding="utf-8-sig")
    faltantes = set(COLUNAS) - set(df.columns)
    if faltantes or df.empty:
        raise ValueError(f"CSV vazio ou sem colunas obrigatórias: {sorted(faltantes)}")
    df = df[COLUNAS].copy()
    if df.isna().any().any():
        raise ValueError("CSV contém campos obrigatórios nulos.")
    for coluna in ("id_subsistema", "nom_subsistema"):
        df[coluna] = df[coluna].astype("str")
        if df[coluna].str.strip().eq("").any():
            raise ValueError(f"Campo vazio: {coluna}")
    df["din_instante"] = pd.to_datetime(df["din_instante"], errors="raise")
    datas = df["din_instante"]
    if (datas.dt.tz is not None or not datas.dt.year.eq(ano).all()
            or not datas.eq(datas.dt.normalize()).all()):
        raise ValueError(f"Datas fora da granularidade diária ou do ano {ano}.")
    valores = pd.to_numeric(df["val_cargaenergiamwmed"], errors="raise").astype("float64")
    if not (valores.gt(0) & valores.lt(float("inf"))).all():
        raise ValueError("Carga deve ser finita e positiva, em MWmed.")
    df["val_cargaenergiamwmed"] = valores
    if df.duplicated(["din_instante", "id_subsistema"]).any():
        raise ValueError("Chave diária por subsistema duplicada.")
    if set(df["id_subsistema"]) != SUBSISTEMAS:
        raise ValueError("Conjunto de subsistemas inesperado.")
    if not df.groupby("id_subsistema")["nom_subsistema"].nunique().eq(1).all():
        raise ValueError("Nomes inconsistentes para o mesmo subsistema.")
    por_dia = df.groupby("din_instante")["id_subsistema"].nunique()
    esperado = pd.date_range(f"{ano}-01-01", datas.max(), freq="D")
    if not por_dia.eq(4).all() or not por_dia.index.equals(esperado):
        raise ValueError("Lacunas de datas ou subsistemas na carga diária.")
    return df


def salvar_processed(df: pd.DataFrame, destino: Path) -> None:
    if destino.exists():
        try:
            if pd.read_parquet(destino).equals(df):
                return
        except (OSError, ValueError):
            pass  # O CSV validado permite reconstruir o intermediário.
    with tempfile.NamedTemporaryFile(dir=destino.parent, suffix=".tmp", delete=False) as arquivo:
        temporario = Path(arquivo.name)
    try:
        df.to_parquet(temporario, index=False, engine="pyarrow")
        temporario.replace(destino)
    finally:
        temporario.unlink(missing_ok=True)


def salvar_estado(estado: dict, destino: Path) -> None:
    with tempfile.NamedTemporaryFile(dir=destino.parent, suffix=".tmp", delete=False) as arquivo:
        temporario = Path(arquivo.name)
    try:
        temporario.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
        temporario.replace(destino)
    finally:
        temporario.unlink(missing_ok=True)


@repetir_transitorios
def atualizar_ano(sessao: requests.Session, ano: int, url: str,
                  pasta_dados: Path, estado: dict) -> str:
    proteger_area_operacional(pasta_dados)
    raw = pasta_dados / "raw" / f"CARGA_ENERGIA_{ano}.csv"
    processed = pasta_dados / "processed" / f"carga_energia_{ano}.parquet"
    proteger_area_operacional(raw.parent)
    proteger_area_operacional(processed.parent)
    anterior = estado.get(str(ano), {})
    hash_local = sha256(raw) if raw.exists() else None
    headers = {}
    # Controle legado sem SHA exige download incondicional antes da migração.
    cache_valido = False
    if (hash_local and anterior.get("url") == url
            and anterior.get("sha256") == hash_local
            and anterior.get("tamanho_bytes", raw.stat().st_size) == raw.stat().st_size):
        try:
            df_local = ler_carga(raw, ano)
            cache_valido = True
        except (OSError, ValueError):
            pass
    if cache_valido:
        if anterior.get("etag"):
            headers["If-None-Match"] = anterior["etag"]
        if anterior.get("last_modified"):
            headers["If-Modified-Since"] = anterior["last_modified"]

    temporario = None
    try:
        for tentativa in range(2):
            with sessao.get(url, headers=headers, stream=True, timeout=TIMEOUT,
                            allow_redirects=False) as resposta:
                if resposta.status_code == 304:
                    if not cache_valido or not headers or not raw.exists() or sha256(raw) != hash_local:
                        if tentativa == 0:
                            headers = {}
                            continue
                        raise ValueError("HTTP 304 sem cache local validado; recuperação incondicional falhou.")
                    df_local = ler_carga(raw, ano)
                    df = df_local
                    novo_hash = hash_local
                    etag = resposta.headers.get("ETag") or anterior.get("etag")
                    modificado = resposta.headers.get("Last-Modified") or anterior.get("last_modified")
                else:
                    resposta.raise_for_status()
                    if resposta.status_code != 200:
                        raise ValueError(f"Resposta inesperada no download: HTTP {resposta.status_code}")
                    with tempfile.NamedTemporaryFile(dir=raw.parent, suffix=".part", delete=False) as arquivo:
                        temporario = Path(arquivo.name)
                        for bloco in resposta.iter_content(chunk_size=1024 * 1024):
                            if bloco:
                                arquivo.write(bloco)
                    df = ler_carga(temporario, ano)
                    if cache_valido and df["din_instante"].max() < df_local["din_instante"].max():
                        raise ValueError("CSV reduz a cobertura já validada; preserve o histórico e investigue.")
                    novo_hash = sha256(temporario)
                    etag = resposta.headers.get("ETag")
                    modificado = resposta.headers.get("Last-Modified")
            break

        # Só promove Raw depois de validar o CSV e materializar seu intermediário.
        alterado = novo_hash != hash_local
        novo_estado = {
            **anterior,
            "url": url, "etag": etag, "last_modified": modificado,
            "sha256": novo_hash, "tamanho_bytes": (temporario or raw).stat().st_size,
            "registros": len(df),
            "ultima_data": df["din_instante"].max().date().isoformat(),
            "baixado_em": (datetime.now(timezone.utc).isoformat() if alterado
                           else anterior.get("baixado_em")),
            "verificado_em": datetime.now(timezone.utc).isoformat(),
        }
        # O estado só confirma um ano depois de ambos os arquivos estarem prontos.
        atualizado = {**estado, str(ano): novo_estado}
        backup_processed = None
        havia_processed = processed.exists()
        try:
            if havia_processed:
                with tempfile.NamedTemporaryFile(dir=processed.parent, suffix=".rollback", delete=False) as arquivo:
                    backup_processed = Path(arquivo.name)
                shutil.copyfile(processed, backup_processed)
            salvar_processed(df, processed)
            if temporario is not None and alterado:
                publicar(temporario, raw, lambda: salvar_estado(atualizado, pasta_dados / "controle_ons.json"),
                         pasta_dados / "controle_ons.json")
            else:
                salvar_estado(atualizado, pasta_dados / "controle_ons.json")
        except Exception:
            if backup_processed is not None:
                backup_processed.replace(processed)
            elif not havia_processed:
                processed.unlink(missing_ok=True)
            raise
        finally:
            if backup_processed is not None:
                backup_processed.unlink(missing_ok=True)
        estado.update(atualizado)
        return "atualizado" if alterado else "sem_alteracao"
    finally:
        if temporario is not None:
            temporario.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anos", nargs="+", type=int, required=True,
                        help="Anos anuais a consultar, por exemplo: 2024 2025 2026.")
    parser.add_argument("--data-dir", type=Path, default=BASE_DIR / "data",
                        help="Diretório de dados; padrão: data/ da raiz do projeto.")
    args = parser.parse_args()
    if any(not 1900 <= ano <= 9999 for ano in args.anos):
        parser.error("Anos devem ter quatro dígitos e ser maiores ou iguais a 1900.")
    pasta = args.data_dir.resolve()
    proteger_area_operacional(pasta)
    estado = carregar_estado(pasta / "controle_ons.json")
    with requests.Session() as sessao:
        recursos = consultar_recursos(sessao, sorted(set(args.anos)))
        for nome in ("raw", "processed"):
            (pasta / nome).mkdir(parents=True, exist_ok=True)
        for ano, url in sorted(recursos.items()):
            status = atualizar_ano(sessao, ano, url, pasta, estado)
            print(f"{ano}: {status}; {estado[str(ano)]['registros']} registros")


if __name__ == "__main__":
    main()
