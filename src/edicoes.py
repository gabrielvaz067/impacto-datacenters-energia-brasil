"""Preserva, verifica, transporta e reproduz edições sem consultar fontes correntes."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

import pandas as pd

try:
    import snapshots
except ModuleNotFoundError:
    from src import snapshots

BASE_DIR = Path(__file__).resolve().parent.parent
ANEEL_RAW = "data/raw/aneel/siga_empreendimentos_diario.csv"


def ler_controle(origem, nome):
    arquivo = origem / "data" / nome
    return json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}


def registrar(arquivo, caminho, fonte, dataset, recurso, referencia, controle=None, tipo="dados"):
    controle = controle or {}
    item = {
        "tipo": tipo, "fonte": fonte, "dataset": dataset, "recurso": recurso,
        "referencia": referencia, "data_aquisicao": controle.get("baixado_em"),
        "situacao_aquisicao": "registrada" if controle.get("baixado_em") else "nao_registrada",
        "caminho": caminho, "tamanho_bytes": arquivo.stat().st_size,
        "sha256": snapshots.sha256(arquivo),
        # Observações do controle local, NÃO uma consulta atual à fonte.
        "metadados_origem_registrados": controle,
    }
    if controle.get("sha256") and controle["sha256"] != item["sha256"]:
        raise ValueError(f"SHA-256 diverge do controle de origem: {caminho}")
    return item


def inventariar_dados(origem, configuracao=None):
    origem = Path(origem)
    cfg = dict(configuracao or {})
    anos = cfg.get("carga_anos") or sorted(int(p.stem.rsplit("_", 1)[1])
                                          for p in (origem / "data/raw").glob("CARGA_ENERGIA_*.csv"))
    meses = cfg.get("geracao_competencias") or sorted(
        "-".join(p.stem.split("_")[-2:]) for p in (origem / "data/raw/ons/geracao").rglob("*.parquet"))
    if not anos or not meses or len(anos) != len(set(anos)) or len(meses) != len(set(meses)):
        raise ValueError("Edição exige anos de carga e competências de geração únicos e não vazios.")
    carga = ler_controle(origem, "controle_ons.json")
    geracao = ler_controle(origem, "controle_geracao_ons.json")
    aneel = ler_controle(origem, "controle_aneel.json")
    itens = []
    ultima_carga, ultima_geracao = [], []
    for ano in sorted(anos):
        if type(ano) is not int or not 1900 <= ano <= 9999:
            raise ValueError("Ano de carga inválido.")
        relativo = f"data/raw/CARGA_ENERGIA_{ano}.csv"
        arquivo = snapshots.caminho_seguro(origem, relativo)
        datas = pd.to_datetime(pd.read_csv(arquivo, sep=";", usecols=["din_instante"])["din_instante"])
        ultima_carga.append(datas.max())
        info = carga.get(str(ano), {})
        itens.append(registrar(arquivo, relativo, "ONS", "carga-energia",
                              info.get("url") or f"Carga_Energia-{ano}", str(ano), info))
    for mes in sorted(meses):
        if not isinstance(mes, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", mes):
            raise ValueError("Competência de geração inválida.")
        ano, numero = mes.split("-")
        relativo = f"data/raw/ons/geracao/{ano}/geracao_usina_{ano}_{numero}.parquet"
        arquivo = snapshots.caminho_seguro(origem, relativo)
        ultima_geracao.append(pd.to_datetime(pd.read_parquet(arquivo, columns=["din_instante"])
                                             ["din_instante"]).max())
        info = geracao.get(mes, {})
        itens.append(registrar(arquivo, relativo, "ONS", "geracao-usina-2",
                              info.get("url") or f"Geracao_Usina_2-{mes}", mes, info))
    arquivo_aneel = origem / ANEEL_RAW
    datas = pd.to_datetime(pd.read_csv(arquivo_aneel, sep=";", encoding="utf-8-sig",
                                      usecols=["DatGeracaoConjuntoDados"])["DatGeracaoConjuntoDados"])
    if datas.isna().any() or datas.dt.normalize().nunique() != 1:
        raise ValueError("Fotografia ANEEL sem uma data de referência única.")
    referencia_aneel = datas.iloc[0].date().isoformat()
    itens.append(registrar(arquivo_aneel, ANEEL_RAW, "ANEEL", "siga-sistema-de-informacoes-de-geracao-da-aneel",
                          aneel.get("url") or "siga-empreendimentos-geracao-diario.csv", referencia_aneel, aneel))
    relativo_epe = "data/reference/epe_pde2036.csv"
    arquivo_epe = origem / relativo_epe
    epe = pd.read_csv(arquivo_epe)
    if epe["versao"].isna().any() or epe["versao"].nunique() != 1:
        raise ValueError("Referência EPE sem versão única.")
    versao_epe = str(epe["versao"].iloc[0])
    itens.append(registrar(arquivo_epe, relativo_epe, "EPE", "PDE 2036 - curadoria CSV",
                          str(epe["url_fonte"].iloc[0]), versao_epe))
    observada = {
        "carga_anos": sorted(anos), "carga_ultima_data": max(ultima_carga).date().isoformat(),
        "geracao_competencias": sorted(meses), "geracao_ultima_data": max(ultima_geracao).isoformat(),
        "aneel_referencia": referencia_aneel, "epe_versao": versao_epe,
    }
    for chave, valor in observada.items():
        if chave in cfg and cfg[chave] != valor:
            raise ValueError(f"Configuração {chave} difere dos arquivos disponíveis: {valor}")
    return itens, {**cfg, **observada}


def preservar(edicao, origem, armazenamento, destino, configuracao=None, projeto=BASE_DIR):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", edicao):
        raise ValueError("Identificador de edição inválido.")
    origem, projeto = Path(origem).resolve(), Path(projeto).resolve()
    itens, config = inventariar_dados(origem, configuracao)
    arquivos = {a["caminho"]: origem / a["caminho"] for a in itens}
    # Preserva também o código para que uma mudança futura não altere a edição.
    for padrao in ("src/*.py", "notebooks/*.ipynb", "sql/*.sql", "requirements.txt"):
        for arquivo in sorted(projeto.glob(padrao)):
            relativo = arquivo.relative_to(projeto).as_posix()
            itens.append(registrar(arquivo, relativo, "Projeto", "implementacao_analitica",
                                  relativo, edicao, tipo="implementacao"))
            arquivos[relativo] = arquivo
    manifesto = {
        "versao_formato": 1, "edicao": edicao, "preservado_em": snapshots.agora(),
        "configuracao": config,
        "aquisicao_legada": "Datas ausentes são desconhecidas; verificado_em/Last-Modified não são aquisição.",
        "distribuicao": {"status": "pendente_publicacao", "url_pacote": None},
        "arquivos": sorted(itens, key=lambda a: a["caminho"]),
    }
    snapshots.validar_manifesto(manifesto)
    if Path(destino).exists():
        # Confere conflito antes de inserir qualquer objeto novo.
        anterior = snapshots.ler_manifesto(destino)
        if {**manifesto, "preservado_em": anterior["preservado_em"]} != anterior:
            raise FileExistsError("Edição já preservada. Use um novo identificador para a revisão.")
    for item in manifesto["arquivos"]:
        snapshots.guardar(arquivos[item["caminho"]], armazenamento, item)
    return snapshots.publicar_manifesto(manifesto, destino)


def conferir_area(manifesto, raiz):
    for item in manifesto["arquivos"]:
        snapshots.conferir(snapshots.caminho_seguro(raiz, item["caminho"]), item)
    esperados = {a["caminho"] for a in manifesto["arquivos"] if a["tipo"] == "dados"}
    presentes = {p.relative_to(raiz).as_posix() for pasta in ("data/raw", "data/reference")
                 for p in (raiz / pasta).rglob("*") if p.is_file()}
    if presentes != esperados:
        raise ValueError("Área de reprodução contém insumos extras ou ausentes.")


def reproduzir(manifesto_path, armazenamento, destino, executar=True):
    manifesto = snapshots.ler_manifesto(manifesto_path)
    snapshots.verificar(manifesto, armazenamento)
    destino = Path(destino).resolve()
    marcador = destino / snapshots.MANIFESTO_LOCAL
    if destino.exists() and any(destino.iterdir()):
        if not marcador.exists() or snapshots.ler_manifesto(marcador) != manifesto:
            raise FileExistsError("Destino ocupado por outra execução; use uma pasta nova.")
    else:
        snapshots.proteger_area_operacional(destino)
        destino.mkdir(parents=True, exist_ok=True)
        snapshots.publicar_manifesto(manifesto, marcador)
    for item in manifesto["arquivos"]:
        snapshots.restaurar_arquivo(manifesto, armazenamento, item["caminho"],
                                    snapshots.caminho_seguro(destino, item["caminho"]))
    conferir_area(manifesto, destino)
    if executar:
        # Usa o executável/código preservado, não o código de uma edição futura.
        subprocess.run([sys.executable, "-B", "src/edicoes.py", "transformar"], cwd=destino, check=True)
    return manifesto


def transformar():
    """Executado dentro da reprodução; offline e sem recriar a curadoria EPE."""
    raiz = BASE_DIR
    manifesto = snapshots.ler_manifesto(raiz / snapshots.MANIFESTO_LOCAL)
    conferir_area(manifesto, raiz)
    try:
        from extracao_ons import ler_carga, salvar_processed
    except ModuleNotFoundError:
        from src.extracao_ons import ler_carga, salvar_processed
    pasta = raiz / "data/processed"
    pasta.mkdir(exist_ok=True)
    anos = manifesto["configuracao"]["carga_anos"]
    permitidos = {f"carga_energia_{a}.parquet" for a in anos}
    if {p.name for p in pasta.glob("carga_energia_*.parquet")} - permitidos:
        raise ValueError("Processed contém anos fora da edição.")
    for ano in anos:
        salvar_processed(ler_carga(raiz / f"data/raw/CARGA_ENERGIA_{ano}.csv", ano),
                         pasta / f"carga_energia_{ano}.parquet")
    for nome in ("transformacao_ons.py", "transformacao_aneel.py", "transformacao_geracao_ons.py"):
        subprocess.run([sys.executable, "-B", f"src/{nome}"], cwd=raiz, check=True)


def comparar(manifesto, dados_atuais):
    resultado = []
    for item in manifesto["arquivos"]:
        if item["tipo"] != "dados":
            continue
        relativo = item["caminho"].removeprefix("data/")
        arquivo = snapshots.caminho_seguro(dados_atuais, relativo)
        atual = snapshots.sha256(arquivo) if arquivo.is_file() else None
        resultado.append({"caminho": item["caminho"], "sha256_edicao": item["sha256"],
                          "sha256_atual": atual, "situacao": "ausente" if atual is None else
                          ("identico" if atual == item["sha256"] else "conteudo_divergente")})
    return resultado


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    comandos = parser.add_subparsers(dest="comando", required=True)
    preservar_cli = comandos.add_parser("preservar", help="Congelar arquivos locais em uma nova edição.")
    preservar_cli.add_argument("--edicao", required=True)
    preservar_cli.add_argument("--origem", type=Path, default=BASE_DIR)
    preservar_cli.add_argument("--config", type=Path, help="Opcional: exige o recorte declarado neste JSON.")
    preservar_cli.add_argument("--manifestos", type=Path, default=BASE_DIR / "edicoes")
    preservar_cli.add_argument("--armazenamento", type=Path, default=BASE_DIR / "data/snapshots")
    for nome in ("verificar", "reproduzir", "exportar", "importar", "comparar"):
        comando = comandos.add_parser(nome)
        comando.add_argument("--manifesto", type=Path, required=True)
        comando.add_argument("--armazenamento", type=Path, default=BASE_DIR / "data/snapshots")
        if nome == "reproduzir":
            comando.add_argument("--destino", type=Path, required=True)
        elif nome in ("exportar", "importar"):
            comando.add_argument("--pacote", type=Path, required=True)
        elif nome == "comparar":
            comando.add_argument("--dados-atuais", type=Path, required=True)
    comandos.add_parser("transformar", help="Regenerar derivados dentro de uma reprodução já verificada.")
    args = parser.parse_args()
    if args.comando == "preservar":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", args.edicao):
            parser.error("Identificador de edição inválido.")
        config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else None
        preservar(args.edicao, args.origem, args.armazenamento,
                  args.manifestos / f"{args.edicao}.json", config)
        print(f"Edição preservada e verificada: {args.edicao}")
    elif args.comando == "transformar":
        transformar()
    elif args.comando == "reproduzir":
        reproduzir(args.manifesto, args.armazenamento, args.destino)
        print(f"Reprodução offline concluída: {args.destino}")
    elif args.comando == "comparar":
        print(json.dumps(comparar(snapshots.ler_manifesto(args.manifesto), args.dados_atuais), indent=2))
    elif args.comando == "verificar":
        manifesto = snapshots.ler_manifesto(args.manifesto)
        snapshots.verificar(manifesto, args.armazenamento)
        print(f"Integridade OK: {manifesto['edicao']}; {len(manifesto['arquivos'])} arquivos")
    elif args.comando == "exportar":
        snapshots.exportar(args.manifesto, args.armazenamento, args.pacote)
        print(f"Pacote criado: {args.pacote}")
    elif args.comando == "importar":
        snapshots.importar(args.manifesto, args.armazenamento, args.pacote)
        print("Pacote importado e verificado contra o manifesto fornecido.")


if __name__ == "__main__":
    main()
