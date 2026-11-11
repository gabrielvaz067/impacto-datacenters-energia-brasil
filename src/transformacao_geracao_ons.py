
from pathlib import Path
import argparse
import re
import pandas as pd
try:
    from publicacao import Publicacao
except ModuleNotFoundError:
    from src.publicacao import Publicacao


# 1. CONFIGURAÇÕES
BASE_DIR = Path(__file__).resolve().parent.parent

RAW_DIR = BASE_DIR / "data/raw/ons/geracao"
SILVER_DIR = BASE_DIR / "data/silver/ons/geracao"
GOLD_DIR = BASE_DIR / "data/gold/ons/geracao"

SILVER_DIR.mkdir(parents=True, exist_ok=True)
GOLD_DIR.mkdir(parents=True, exist_ok=True)

COLUNAS = [
    "din_instante",
    "id_subsistema",
    "nom_subsistema",
    "id_estado",
    "nom_estado",
    "cod_modalidadeoperacao",
    "nom_tipousina",
    "nom_tipocombustivel",
    "nom_usina",
    "id_ons",
    "ceg",
    "val_geracao",
]

CHAVE_REGISTRO = [
    "din_instante",
    "id_subsistema",
    "id_estado",
    "cod_modalidadeoperacao",
    "nom_tipousina",
    "nom_usina",
]



# 2. SILVER
def criar_silver(arquivo, destino=None):

    print(f"\nProcessando {arquivo.name}...")

    import pyarrow.parquet as pq
    esquema = pq.read_schema(arquivo)
    faltantes = set(COLUNAS) - set(esquema.names)

    if faltantes:
        raise ValueError(
            f"Colunas ausentes: {sorted(faltantes)}"
        )

    df = pd.read_parquet(arquivo, columns=COLUNAS)

    df["din_instante"] = pd.to_datetime(
        df["din_instante"],
        errors="raise",
    )

    df["val_geracao"] = pd.to_numeric(
        df["val_geracao"],
        errors="raise",
    )

    if df["val_geracao"].isin([float("inf"), float("-inf")]).any():
        raise ValueError("Valores infinitos em val_geracao.")

    if df.empty:
        raise ValueError("Arquivo sem registros.")

    if df["din_instante"].isna().any():
        raise ValueError("Datas nulas encontradas.")

    if df.duplicated(CHAVE_REGISTRO).any():
        qtd = df.duplicated(
            CHAVE_REGISTRO, keep=False
        ).sum()

        raise ValueError(
            f"Chave de registro repetida: {qtd} linhas. "
            "Investigue antes de deduplicar."
        )

    # Competência
    df["ano"] = df["din_instante"].dt.year
    df["mes"] = df["din_instante"].dt.month
    df["hora"] = df["din_instante"].dt.hour

    ano = int(df["ano"].iloc[0])
    mes = int(df["mes"].iloc[0])

    if not (
        df["ano"].eq(ano) & df["mes"].eq(mes)
    ).all():
        raise ValueError(
            "Arquivo contém mais de uma competência."
        )

    nome = re.fullmatch(r"geracao_usina_(\d{4})_(\d{2})\.parquet", arquivo.name)
    if nome and (int(nome.group(1)), int(nome.group(2))) != (ano, mes):
        raise ValueError("Competência do conteúdo diverge do nome do arquivo.")
    if not df["din_instante"].eq(df["din_instante"].dt.floor("h")).all():
        raise ValueError("Geração contém instantes fora da hora exata.")

    # Classificação da modalidade
    df["grupo_modalidade"] = "Demais modalidades"

    df.loc[
        df["cod_modalidadeoperacao"].eq(
            "Pequenas Usinas (Tipo III)"
        ),
        "grupo_modalidade",
    ] = "Pequenas usinas Tipo III"

    df.loc[
        df["cod_modalidadeoperacao"].eq(
            "Pequenas Usinas (MMGD)"
        ),
        "grupo_modalidade",
    ] = "MMGD"

    df.loc[
        df["cod_modalidadeoperacao"].eq(
            "Conjunto de Usinas"
        ),
        "grupo_modalidade",
    ] = "Conjuntos de usinas"

    # Indicadores de qualidade
    df["geracao_ausente"] = (
        df["val_geracao"].isna()
    )

    df["geracao_negativa"] = (
        df["val_geracao"].lt(0)
    )

    df["geracao_zero"] = (
        df["val_geracao"].eq(0)
    )

    df["geracao_positiva"] = (
        df["val_geracao"].gt(0)
    )

    df["arquivo_origem"] = arquivo.name

    # Preserva o valor original, inclusive negativo
    destino = destino or (
        SILVER_DIR /
        f"geracao_{ano}_{mes:02d}.parquet"
    )

    temporario = destino.with_suffix(".tmp")

    df.to_parquet(
        temporario,
        index=False,
        engine="pyarrow",
    )

    temporario.replace(destino)

    print(f"Registros: {len(df):,}")
    print(
        "Nulos:",
        f"{df['geracao_ausente'].sum():,}"
    )
    print(
        "Negativos:",
        f"{df['geracao_negativa'].sum():,}"
    )
    print(
        "Zerados:",
        f"{df['geracao_zero'].sum():,}"
    )
    print(f"Silver criada: {destino.name}")

    return df


# 3. GOLD HORÁRIA
def criar_gold_horaria(df):

    dimensoes = [
        "din_instante",
        "id_subsistema",
        "nom_tipousina",
        "cod_modalidadeoperacao",
        "grupo_modalidade",
    ]

    gold = (
        df.groupby(
            dimensoes,
            as_index=False,
            dropna=False,
        )
        .agg(
            registros=("val_geracao", "size"),
            registros_validos=("val_geracao", "count"),
            valores_ausentes=("geracao_ausente", "sum"),
            valores_negativos=("geracao_negativa", "sum"),
            valores_zero=("geracao_zero", "sum"),
            geracao_soma=(
                "val_geracao",
                lambda valores: valores.sum(
                    min_count=1
                ),
            ),
        )
    )

    gold["cobertura_pct"] = (
        gold["registros_validos"]
        / gold["registros"] * 100
    )

    gold["ano"] = gold["din_instante"].dt.year
    gold["mes"] = gold["din_instante"].dt.month
    gold["hora"] = gold["din_instante"].dt.hour

    return gold


# 4. GOLD DE QUALIDADE
def criar_gold_qualidade(df):

    dimensoes = [
        "ano",
        "mes",
        "cod_modalidadeoperacao",
        "nom_tipousina",
    ]

    gold = (
        df.groupby(
            dimensoes,
            as_index=False,
            dropna=False,
        )
        .agg(
            registros=("val_geracao", "size"),
            validos=("val_geracao", "count"),
            nulos=("geracao_ausente", "sum"),
            negativos=("geracao_negativa", "sum"),
            zerados=("geracao_zero", "sum"),
        )
    )

    gold["cobertura_pct"] = (
        gold["validos"] / gold["registros"] * 100
    )

    gold["percentual_negativos"] = (
        gold["negativos"] / gold["registros"] * 100
    )

    return gold


# 5. VALIDAÇÃO
def validar_resultados(silver_list, horario, qualidade):

    total_raw = sum(len(df) if isinstance(df, pd.DataFrame) else df["registros"] for df in silver_list)

    total_horario = horario["registros"].sum()
    total_qualidade = qualidade["registros"].sum()

    if not (
        total_raw == total_horario == total_qualidade
    ):
        raise ValueError(
            "Divergência na contagem de registros "
            "entre Silver e Gold."
        )

    nulos_silver = sum(
        int(df["geracao_ausente"].sum()) if isinstance(df, pd.DataFrame) else df["nulos"]
        for df in silver_list
    )

    negativos_silver = sum(
        int(df["geracao_negativa"].sum()) if isinstance(df, pd.DataFrame) else df["negativos"]
        for df in silver_list
    )

    if (
        horario["valores_ausentes"].sum()
        != nulos_silver
    ):
        raise ValueError(
            "Divergência na contagem de nulos."
        )

    if (
        horario["valores_negativos"].sum()
        != negativos_silver
    ):
        raise ValueError(
            "Divergência na contagem de negativos."
        )

    chave_gold = [
        "din_instante",
        "id_subsistema",
        "nom_tipousina",
        "cod_modalidadeoperacao",
    ]

    if horario.duplicated(chave_gold).any():
        raise ValueError(
            "Chaves repetidas na Gold horária."
        )

    if not horario["cobertura_pct"].between(
        0, 100
    ).all():
        raise ValueError(
            "Cobertura fora do intervalo esperado."
        )

    print("\nValidação Silver x Gold: OK")


# 6. GRAVAÇÃO DAS GOLDS
def salvar_parquet(df, destino):

    temporario = destino.with_suffix(".tmp")

    df.to_parquet(
        temporario,
        index=False,
        engine="pyarrow",
    )

    temporario.replace(destino)


# 7. EXECUÇÃO PRINCIPAL
def diagnosticar_mes(df):
    """Resume uma competencia sem reter a Silver detalhada."""
    chave = f"{int(df['ano'].iloc[0]):04d}-{int(df['mes'].iloc[0]):02d}"
    horas = pd.DatetimeIndex(df["din_instante"].drop_duplicates().sort_values())
    esperadas_internas = pd.date_range(horas.min(), horas.max(), freq="h")
    if len(horas) != len(esperadas_internas):
        raise ValueError(f"Horas internas ausentes em {chave}.")
    primeiro = pd.Timestamp(chave + "-01")
    proximo = primeiro + pd.offsets.MonthBegin(1)
    quantidade = int((proximo - primeiro) / pd.Timedelta(hours=1))
    colunas_entidade = [c for c in CHAVE_REGISTRO if c != "din_instante"]
    dados = df[colunas_entidade].astype("string").fillna("").drop_duplicates()
    entidades = set(dados.itertuples(index=False, name=None))
    base_presenca = df[colunas_entidade + ["din_instante"]].copy()
    for coluna in colunas_entidade:
        base_presenca[coluna] = base_presenca[coluna].astype("string").fillna("")
    presenca = base_presenca.groupby(colunas_entidade, dropna=False)["din_instante"].nunique()
    parciais = presenca[presenca < len(horas)]
    detalhe_parciais = [{"entidade": list(entidade), "horas_presentes": int(total)}
                        for entidade, total in parciais.items()]
    metricas = {"horas_recebidas": len(horas), "horas_no_mes": quantidade,
                      "primeiro_instante": horas.min().isoformat(), "ultimo_instante": horas.max().isoformat(),
                      "mes_completo": len(horas) == quantidade, "entidades": len(entidades),
                      "registros": len(df), "valores_preenchidos": int(df["val_geracao"].notna().sum()),
                      "nulos": int(df["val_geracao"].isna().sum()),
                      "negativos": int(df["val_geracao"].lt(0).sum()),
                      "entidades_com_horas_ausentes": detalhe_parciais}
    return chave, metricas, entidades


def diagnosticar_periodo(silvers, inicio=None, fim=None):
    """Aceita Silvers ou resumos mensais e compara seus universos."""
    por_mes = {}
    entidades = {}
    for item in silvers:
        chave, metricas, universo = diagnosticar_mes(item) if isinstance(item, pd.DataFrame) else item
        if chave in por_mes:
            raise ValueError(f"Competencia de geracao duplicada: {chave}")
        por_mes[chave] = metricas
        entidades[chave] = universo
    meses = sorted(por_mes)
    desejados = pd.period_range(inicio or meses[0], fim or meses[-1], freq="M")
    solicitados = [str(m) for m in desejados]
    if meses != solicitados:
        raise ValueError(f"Competências solicitadas ausentes ou extras: esperadas {solicitados}, recebidas {meses}")
    mudancas = []
    for anterior, atual in zip(meses, meses[1:]):
        entradas = sorted(entidades[atual] - entidades[anterior])
        saidas = sorted(entidades[anterior] - entidades[atual])
        mudancas.append({"anterior": anterior, "atual": atual, "entradas": len(entradas), "saidas": len(saidas),
                         "entidades_entrantes": entradas, "entidades_saintes": saidas})
    alertas = [f"Competência parcial: {m}" for m in meses if not por_mes[m]["mes_completo"]]
    alertas += [f"Entidades com horas ausentes em {m}: {len(por_mes[m]['entidades_com_horas_ausentes'])}"
                for m in meses if por_mes[m]["entidades_com_horas_ausentes"]]
    alertas += [f"Universo alterado de {m['anterior']} para {m['atual']}: {m['entradas']} entradas, {m['saidas']} saídas"
                for m in mudancas if m["entradas"] or m["saidas"]]
    return {"inicio_solicitado": inicio, "fim_solicitado": fim, "competencias": por_mes,
            "mudancas_universo": mudancas, "alertas": alertas}


def main(inicio=None, fim=None):

    print("=== TRANSFORMAÇÃO GERAÇÃO ONS ===")

    arquivos = sorted(
        RAW_DIR.rglob("*.parquet")
    )

    if not arquivos:
        raise FileNotFoundError(
            "Nenhum arquivo Parquet encontrado."
        )

    print(f"Arquivos encontrados: {len(arquivos)}")

    resumos_mensais = []
    gold_horaria_list = []
    gold_qualidade_list = []

    with Publicacao(BASE_DIR, "geracao_ons") as publicacao:
        caminhos = []
        for arquivo in arquivos:
            nome = re.fullmatch(r"geracao_usina_(\d{4})_(\d{2})\.parquet", arquivo.name)
            if not nome:
                raise ValueError(f"Nome de recurso mensal inesperado: {arquivo.name}")
            relativo = f"data/silver/ons/geracao/geracao_{nome.group(1)}_{nome.group(2)}.parquet"
            if relativo in caminhos:
                raise ValueError(f"Competencia de geracao duplicada: {relativo}")
            caminhos.append(relativo)
            silver = criar_silver(arquivo, publicacao.caminho(relativo))
            resumos_mensais.append(diagnosticar_mes(silver))
            gold_horaria_list.append(criar_gold_horaria(silver))
            gold_qualidade_list.append(criar_gold_qualidade(silver))
            del silver

        horario = pd.concat(gold_horaria_list, ignore_index=True)
        qualidade = pd.concat(gold_qualidade_list, ignore_index=True)
        validar_resultados([item[1] for item in resumos_mensais], horario, qualidade)
        diagnostico = diagnosticar_periodo(resumos_mensais, inicio, fim)
        extras = {p.name for p in SILVER_DIR.glob("geracao_*.parquet")} - {Path(c).name for c in caminhos}
        if extras:
            raise ValueError(f"Silvers anteriores fora do periodo: {sorted(extras)}. Use outra area de dados.")
        horario_rel = "data/gold/ons/geracao/geracao_horaria.parquet"
        qualidade_rel = "data/gold/ons/geracao/qualidade_geracao.parquet"
        salvar_parquet(horario, publicacao.caminho(horario_rel))
        salvar_parquet(qualidade, publicacao.caminho(qualidade_rel))
        publicacao.confirmar(caminhos + [horario_rel, qualidade_rel], diagnostico)
        for alerta in diagnostico["alertas"]:
            print("ALERTA:", alerta)

    print("\n=== RESUMO DA TRANSFORMAÇÃO ===")

    print(
        "Registros Silver:",
        f"{sum(item[1]["registros"] for item in resumos_mensais):,}"
    )

    print(
        "Registros Gold horária:",
        f"{len(horario):,}"
    )

    print(
        "Registros Gold qualidade:",
        f"{len(qualidade):,}"
    )

    print(
        "Período:",
        horario["din_instante"].min(),
        "até",
        horario["din_instante"].max(),
    )

    print(
        "\n=== QUALIDADE POR MODALIDADE ==="
    )

    resumo = (
        qualidade.groupby(
            "cod_modalidadeoperacao",
            as_index=False,
        )
        .agg(
            registros=("registros", "sum"),
            nulos=("nulos", "sum"),
            negativos=("negativos", "sum"),
            zerados=("zerados", "sum"),
        )
    )

    resumo["percentual_nulos"] = (
        resumo["nulos"]
        / resumo["registros"] * 100
    )

    print(resumo.to_string(index=False))

    print(
        "\n=== NEGATIVOS POR MÊS E FONTE ==="
    )

    negativos = qualidade[
        qualidade["negativos"] > 0
    ].copy()

    if negativos.empty:
        print("Nenhum valor negativo encontrado.")
    else:
        print(
            negativos[
                [
                    "ano",
                    "mes",
                    "cod_modalidadeoperacao",
                    "nom_tipousina",
                    "negativos",
                ]
            ].to_string(index=False)
        )

    print("\nTransformação concluída com sucesso!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Transforma geração horária ONS.")
    parser.add_argument("--inicio", help="Primeira competência solicitada (AAAA-MM).")
    parser.add_argument("--fim", help="Última competência solicitada (AAAA-MM).")
    args = parser.parse_args()
    if bool(args.inicio) != bool(args.fim):
        parser.error("Informe --inicio e --fim juntos.")
    if args.inicio and (not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", args.inicio)
                        or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", args.fim)
                        or args.inicio > args.fim):
        parser.error("Intervalo de competências inválido.")
    main(args.inicio, args.fim)
