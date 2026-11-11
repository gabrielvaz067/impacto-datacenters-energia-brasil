
from pathlib import Path
import pandas as pd
try:
    from publicacao import Publicacao
except ModuleNotFoundError:
    from src.publicacao import Publicacao

BASE_DIR = Path(__file__).resolve().parent.parent

PROCESSED_DIR = BASE_DIR / "data" / "processed"
SILVER_DIR = BASE_DIR / "data" / "silver"
GOLD_DIR = BASE_DIR / "data" / "gold"

SILVER_DIR.mkdir(parents=True, exist_ok=True)
GOLD_DIR.mkdir(parents=True, exist_ok=True)


def carregar_dados():
    arquivos = sorted(
        PROCESSED_DIR.glob("carga_energia_*.parquet")
    )

    if not arquivos:
        raise FileNotFoundError(
            "Nenhum arquivo Parquet encontrado."
        )

    bases = [
        pd.read_parquet(arquivo)
        for arquivo in arquivos
    ]

    return pd.concat(bases, ignore_index=True)


def criar_silver(df, destino=None):
    df = df.copy()

    # Padronizar campos
    df = df.rename(columns={
        "din_instante": "data",
        "val_cargaenergiamwmed": "carga_mwmed"
    })

    df["data"] = pd.to_datetime(
        df["data"],
        errors="raise"
    )

    df["carga_mwmed"] = pd.to_numeric(
        df["carga_mwmed"],
        errors="raise"
    )

    chave = ["data", "id_subsistema"]

    if df.duplicated(chave).any():
        raise ValueError(
            "Existem registros duplicados na Silver."
        )

    if df[chave + ["carga_mwmed"]].isna().any().any():
        raise ValueError(
            "Existem campos obrigatórios nulos."
        )

    if df["nom_subsistema"].isna().any() or df.groupby("id_subsistema")["nom_subsistema"].nunique().gt(1).any():
        raise ValueError("Nome de subsistema ausente ou inconsistente.")

    if not df["carga_mwmed"].gt(0).all():
        raise ValueError(
            "Existem valores de carga inválidos."
        )

    if not df["data"].eq(df["data"].dt.normalize()).all():
        raise ValueError("Carga diária contém instante fora da meia-noite.")
    esperados = {"N", "NE", "S", "SE"}
    if set(df["id_subsistema"]) != esperados:
        raise ValueError("Universo de subsistemas diferente dos quatro esperados.")
    por_dia = df.groupby("data")["id_subsistema"].agg(set)
    if not por_dia.map(lambda presentes: presentes == esperados).all():
        raise ValueError("Há dia com subsistema ausente.")
    calendario = pd.date_range(df["data"].min(), df["data"].max(), freq="D")
    if len(por_dia) != len(calendario):
        raise ValueError("Calendário de carga tem datas internas ausentes.")

    # Campos derivados
    df["ano"] = df["data"].dt.year
    df["mes"] = df["data"].dt.month

    # Carga média diária em MW × 24 horas
    df["energia_mwh"] = df["carga_mwmed"] * 24

    df = df.sort_values(chave).reset_index(drop=True)

    destino = destino or SILVER_DIR / "carga_energia.parquet"

    df.to_parquet(destino, index=False)

    print(
        f"Silver criada: {len(df):,} registros."
    )

    return df


def criar_gold(df, destino=None):
    # Agregação mensal por subsistema
    mensal = (
        df.groupby(
            ["ano", "mes", "id_subsistema",
             "nom_subsistema"],
            as_index=False
        )
        .agg(
            energia_mwh=("energia_mwh", "sum"),
            carga_media_mwmed=("carga_mwmed", "mean"),
            dias_observados=("data", "nunique")
        )
    )

    mensal["energia_gwh"] = (
        mensal["energia_mwh"] / 1000
    )

    # Identificar meses completos
    mensal["dias_no_mes"] = pd.to_datetime(
        dict(
            year=mensal["ano"],
            month=mensal["mes"],
            day=1
        )
    ).dt.days_in_month

    mensal["mes_completo"] = (
        mensal["dias_observados"]
        == mensal["dias_no_mes"]
    )

    destino = destino or GOLD_DIR / "carga_mensal.parquet"

    mensal.to_parquet(destino, index=False)

    print(
        f"Gold criada: {len(mensal):,} registros."
    )

    return mensal


def main():
    print("Iniciando transformação ONS...")

    with Publicacao(BASE_DIR, "carga_ons") as publicacao:
        df = carregar_dados()
        silver = criar_silver(df, publicacao.caminho("data/silver/carga_energia.parquet"))
        gold = criar_gold(silver, publicacao.caminho("data/gold/carga_mensal.parquet"))
        if len(silver) != int(gold["dias_observados"].sum()):
            raise ValueError("Contagens Silver e Gold de carga divergem.")
        qualidade = {"inicio": str(silver["data"].min().date()), "fim": str(silver["data"].max().date()),
                     "subsistemas": sorted(silver["id_subsistema"].unique().tolist()),
                     "dias": int(silver["data"].nunique()), "registros": len(silver),
                     "meses_incompletos_por_subsistema": int((~gold["mes_completo"]).sum())}
        if qualidade["meses_incompletos_por_subsistema"]:
            print("ALERTA: há meses incompletos; consulte mes_completo na Gold.")
        publicacao.confirmar(["data/silver/carga_energia.parquet", "data/gold/carga_mensal.parquet"], qualidade)

    print("\nResumo da Gold:")
    print(
        gold.groupby("ano")
        .agg(
            registros=("ano", "size"),
            meses_completos=("mes_completo", "sum")
        )
    )

    print("\nTransformação concluída!")


if __name__ == "__main__":
    main()
