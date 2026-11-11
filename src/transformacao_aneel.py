
from pathlib import Path
from datetime import datetime
import tempfile

import pandas as pd
try:
    from publicacao import Publicacao
except ModuleNotFoundError:
    from src.publicacao import Publicacao


# 1. CONFIGURAÇÕES
BASE_DIR = Path(__file__).resolve().parent.parent

RAW_FILE = (
    BASE_DIR / "data" / "raw" / "aneel"
    / "siga_empreendimentos_diario.csv"
)

SILVER_DIR = BASE_DIR / "data" / "silver" / "aneel"
GOLD_DIR = BASE_DIR / "data" / "gold" / "aneel"

SILVER_DIR.mkdir(parents=True, exist_ok=True)
GOLD_DIR.mkdir(parents=True, exist_ok=True)

COLUNAS = {
    "CodCEG": "cod_ceg",
    "IdeNucleoCEG": "id_nucleo_ceg",
    "NomEmpreendimento": "empreendimento",
    "SigUFPrincipal": "uf",
    "SigTipoGeracao": "tipo_geracao",
    "DscFaseUsina": "fase",
    "DscOrigemCombustivel": "origem",
    "DscFonteCombustivel": "fonte",
    "MdaPotenciaFiscalizadaKw": "potencia_fiscalizada_kw",
    "MdaPotenciaOutorgadaKw": "potencia_outorgada_kw",
    "DatEntradaOperacao": "data_operacao",
    "DatGeracaoConjuntoDados": "data_referencia",
}

ATRIBUTOS_ESSENCIAIS = [
    "id_nucleo_ceg",
    "empreendimento",
    "uf",
    "tipo_geracao",
    "fase",
    "origem",
    "fonte",
    "potencia_fiscalizada_kw",
    "potencia_outorgada_kw",
]


# 2. FUNÇÕES AUXILIARES
def converter_decimal_br(serie):
    """Converte números brasileiros para formato numérico."""

    return pd.to_numeric(
        serie.astype("string")
        .str.strip()
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False),
        errors="coerce"
    )


def salvar_parquet(df, destino):
    """Salva Parquet usando arquivo temporário."""

    temporario = None

    try:
        with tempfile.NamedTemporaryFile(
            dir=destino.parent,
            suffix=".parquet",
            delete=False
        ) as arquivo:
            temporario = Path(arquivo.name)

        df.to_parquet(
            temporario,
            index=False,
            engine="pyarrow"
        )

        temporario.replace(destino)

    finally:
        if temporario is not None:
            temporario.unlink(missing_ok=True)


# 3. TRATAMENTO DE DUPLICIDADES
def tratar_duplicidades(df):
    """Remove apenas CEGs equivalentes para a análise."""

    duplicados = df[
        df.duplicated("cod_ceg", keep=False)
    ].copy()

    if duplicados.empty:
        print("Nenhum CEG duplicado encontrado.")
        return df

    print("\n=== ANÁLISE DE DUPLICIDADES ===")

    print(
        "Registros com CEG repetido:",
        len(duplicados)
    )

    print(
        "CEGs distintos repetidos:",
        duplicados["cod_ceg"].nunique()
    )

    # Detectar divergências nos atributos essenciais
    divergencias = (
        duplicados
        .groupby("cod_ceg")[ATRIBUTOS_ESSENCIAIS]
        .nunique(dropna=False)
    )

    conflitantes = divergencias[
        divergencias.gt(1).any(axis=1)
    ]

    if not conflitantes.empty:
        print("\nCEGs com atributos divergentes:")
        print(conflitantes.to_string())

        raise ValueError(
            "Duplicidades conflitantes identificadas. "
            "É necessária investigação manual."
        )

    # Deduplicação após verificar equivalência
    quantidade_antes = len(df)

    df = df.drop_duplicates(
        subset=["cod_ceg"],
        keep="first"
    ).copy()

    removidos = quantidade_antes - len(df)

    print(
        f"Registros redundantes removidos: {removidos}"
    )

    return df


# 4. CAMADA SILVER
def criar_silver(destino=None):

    print("\n=== CAMADA SILVER ===")

    if not RAW_FILE.exists():
        raise FileNotFoundError(
            f"Arquivo não encontrado: {RAW_FILE}"
        )

    df = pd.read_csv(
        RAW_FILE,
        sep=";",
        encoding="utf-8-sig",
        low_memory=False
    )

    print(f"Registros originais: {len(df):,}")

    # Verificar schema
    faltantes = set(COLUNAS) - set(df.columns)

    if faltantes:
        raise ValueError(
            f"Colunas ausentes: {faltantes}"
        )

    df = df[list(COLUNAS)].rename(
        columns=COLUNAS
    ).copy()

    # Padronização de identificadores
    df["cod_ceg"] = (
        df["cod_ceg"]
        .astype("string")
        .str.strip()
    )

    # Conversão das potências
    df["potencia_fiscalizada_kw"] = pd.to_numeric(
        df["potencia_fiscalizada_kw"],
        errors="coerce"
    )

    df["potencia_outorgada_kw"] = (
        converter_decimal_br(
            df["potencia_outorgada_kw"]
        )
    )

    # Validação antes da deduplicação
    if df["cod_ceg"].isna().any() or df["cod_ceg"].eq("").any():
        raise ValueError("Existem códigos CEG ausentes.")

    if df["fase"].isna().any() or df["fase"].astype("string").str.strip().eq("").any():
        raise ValueError("Existem fases operacionais nulas.")

    if df["potencia_fiscalizada_kw"].isna().any():
        raise ValueError("Potência fiscalizada inválida.")

    if df["potencia_outorgada_kw"].isna().any():
        raise ValueError("Potência outorgada inválida.")

    for coluna in [
        "potencia_fiscalizada_kw",
        "potencia_outorgada_kw"
    ]:
        if df[coluna].lt(0).any():
            raise ValueError(
                f"Potência negativa encontrada: {coluna}"
            )

    # Tratar códigos CEG repetidos
    df = tratar_duplicidades(df)

    # Validar unicidade
    if df["cod_ceg"].duplicated().any():
        raise ValueError(
            "Ainda existem CEGs duplicados."
        )

    # Datas
    for coluna in ["data_operacao", "data_referencia"]:
        df[coluna] = pd.to_datetime(
            df[coluna],
            errors="coerce"
        )

    if df["data_referencia"].isna().any():
        raise ValueError(
            "Data de referência inválida."
        )
    if df["data_referencia"].dt.normalize().nunique() != 1:
        raise ValueError("Fotografia ANEEL contém datas de referência diferentes.")

    # Converter kW para MW
    df["potencia_fiscalizada_mw"] = (
        df["potencia_fiscalizada_kw"] / 1000
    )

    df["potencia_outorgada_mw"] = (
        df["potencia_outorgada_kw"] / 1000
    )

    # Identificação operacional
    df["em_operacao"] = df["fase"].eq("Operação")

    df = df.sort_values(
        "cod_ceg"
    ).reset_index(drop=True)

    destino = destino or SILVER_DIR / "empreendimentos.parquet"

    salvar_parquet(df, destino)

    print(f"Registros Silver: {len(df):,}")
    print(f"Arquivo criado: {destino.name}")

    return df


# 5. GOLD — CAPACIDADE POR FONTE
def criar_gold_fontes(df, destino=None):

    print("\n=== GOLD: CAPACIDADE POR FONTE ===")

    # Apenas empreendimentos em operação
    operacao = df[df["em_operacao"]].copy()

    gold = (
        operacao.groupby(
            ["tipo_geracao", "origem", "fonte"],
            as_index=False,
            dropna=False
        )
        .agg(
            empreendimentos=("cod_ceg", "nunique"),
            potencia_mw=(
                "potencia_fiscalizada_mw",
                "sum"
            )
        )
    )

    total = gold["potencia_mw"].sum()

    if total <= 0:
        raise ValueError(
            "Capacidade total em operação inválida."
        )

    gold["participacao_pct"] = (
        gold["potencia_mw"] / total * 100
    )

    gold = gold.sort_values(
        "potencia_mw",
        ascending=False
    ).reset_index(drop=True)

    salvar_parquet(
        gold,
        destino or GOLD_DIR / "capacidade_por_fonte.parquet"
    )

    print(f"Registros Gold: {len(gold)}")
    print(f"Capacidade total: {total:,.2f} MW")

    return gold


# 6. GOLD — CAPACIDADE POR ESTADO E FASE
def criar_gold_estados(df, destino=None):

    print("\n=== GOLD: CAPACIDADE POR ESTADO ===")

    base = df.copy()

    # Fases diferentes usam métricas diferentes.
    # Nunca somar como capacidade operacional única.
    base["potencia_analise_mw"] = (
        base["potencia_outorgada_mw"]
    )

    base.loc[
        base["em_operacao"],
        "potencia_analise_mw"
    ] = base.loc[
        base["em_operacao"],
        "potencia_fiscalizada_mw"
    ]

    gold = (
        base.groupby(
            ["uf", "fase", "tipo_geracao"],
            as_index=False,
            dropna=False
        )
        .agg(
            empreendimentos=("cod_ceg", "nunique"),
            potencia_mw=("potencia_analise_mw", "sum")
        )
    )

    gold = gold.sort_values(
        ["uf", "fase", "tipo_geracao"]
    ).reset_index(drop=True)

    salvar_parquet(
        gold,
        destino or GOLD_DIR / "capacidade_por_estado.parquet"
    )

    print(f"Registros Gold: {len(gold)}")

    return gold


# 7. RESUMO DE QUALIDADE
def exibir_resumo(silver, gold_fontes):

    print("\n" + "=" * 50)
    print("RESUMO DA TRANSFORMAÇÃO")
    print("=" * 50)

    print(f"Total de empreendimentos: {len(silver):,}")

    print("\nEmpreendimentos por fase:")
    print(
        silver["fase"].value_counts().to_string()
    )

    resumo = (
        gold_fontes.groupby(
            "tipo_geracao",
            as_index=False
        )
        .agg(
            potencia_mw=("potencia_mw", "sum"),
            empreendimentos=("empreendimentos", "sum")
        )
        .sort_values(
            "potencia_mw",
            ascending=False
        )
    )

    total = resumo["potencia_mw"].sum()

    resumo["participacao_pct"] = (
        resumo["potencia_mw"] / total * 100
    )

    print("\nCapacidade em operação por tipo:")
    print(
        resumo.to_string(
            index=False,
            float_format=lambda x: f"{x:,.2f}"
        )
    )

    print(
        f"\nCapacidade operacional total: "
        f"{total:,.2f} MW"
    )

    print(
        "\nData de referência:",
        silver["data_referencia"].max().date()
    )


# 8. EXECUÇÃO PRINCIPAL
def main():

    print("=" * 50)
    print("PIPELINE DE TRANSFORMAÇÃO — ANEEL")
    print("=" * 50)

    with Publicacao(BASE_DIR, "aneel") as publicacao:
        caminhos = ["data/silver/aneel/empreendimentos.parquet",
                    "data/gold/aneel/capacidade_por_fonte.parquet",
                    "data/gold/aneel/capacidade_por_estado.parquet"]
        silver = criar_silver(publicacao.caminho(caminhos[0]))
        gold_fontes = criar_gold_fontes(silver, publicacao.caminho(caminhos[1]))
        gold_estados = criar_gold_estados(silver, publicacao.caminho(caminhos[2]))
        if int(gold_estados["empreendimentos"].sum()) != len(silver):
            raise ValueError("Contagens Silver e Gold ANEEL divergem.")
        qualidade = {"registros": len(silver), "cegs_unicos": int(silver["cod_ceg"].nunique()),
                     "fases": {str(k): int(v) for k, v in silver["fase"].value_counts().items()},
                     "data_referencia": str(silver["data_referencia"].max().date())}
        publicacao.confirmar(caminhos, qualidade)

    exibir_resumo(silver, gold_fontes)

    print("\nTransformação concluída com sucesso!")


if __name__ == "__main__":
    main()
