
from pathlib import Path
import pandas as pd
try:
    from publicacao import Publicacao
except ModuleNotFoundError:
    from src.publicacao import Publicacao

BASE_DIR = Path(__file__).resolve().parent.parent

DESTINO = BASE_DIR / "data" / "reference"
DESTINO.mkdir(parents=True, exist_ok=True)

FONTE = (
    "https://www.epe.gov.br/pt/publicacoes-dados-abertos/"
    "publicacoes/plano-decenal-de-expansao-de-energia-2036"
)

# Curadoria manual dos valores apresentados no PDE 2036; não é extração automática do PDF.
# Não são medições históricas do ONS.
dados = [
    {
        "ano": 2026,
        "indicador": "data_centers",
        "valor_twh": 3,
        "natureza": "estimativa",
    },
    {
        "ano": 2036,
        "indicador": "data_centers",
        "valor_twh": 30,
        "natureza": "projecao",
    },
    {
        "ano": 2026,
        "indicador": "eletricidade_total",
        "valor_twh": 702,
        "natureza": "projecao",
    },
    {
        "ano": 2036,
        "indicador": "eletricidade_total",
        "valor_twh": 979,
        "natureza": "projecao",
    },
]

df = pd.DataFrame(dados)

df["fonte"] = "EPE - PDE 2036"
df["url_fonte"] = FONTE
df["cenario"] = "referencia"
df["versao"] = "consulta_publica_2026"

df["documento"] = "PDE 2036 - Relatório Síntese, consulta pública"
df["url_documento"] = (
    "https://www.epe.gov.br/sites-pt/publicacoes-dados-abertos/publicacoes/"
    "PublicacoesArquivos/publicacao-982/topico-860/"
    "PDE%202036_Relat%C3%B3rio%20S%C3%ADntese_rev3_2026-10-08.pdf"
)
df["versao_documento"] = "outubro/2026; arquivo rev3_2026-10-08"
df["pagina_impressa"] = df["indicador"].map({"data_centers": 60, "eletricidade_total": 59})
df["figura_tabela"] = df["indicador"].map({"data_centers": "Figura 2-12", "eletricidade_total": "Tabela 2-4"})
df["definicao"] = df["indicador"].map({
    "data_centers": "Consumo anual de eletricidade dos data centers no recorte de grandes cargas",
    "eletricidade_total": "Consumo anual total de eletricidade no Brasil",
})
df["perimetro"] = df["indicador"].map({
    "data_centers": "Data centers de grande porte abrangidos pelo estudo; não exclusivamente IA",
    "eletricidade_total": "Classes de consumo, autoprodução não injetada e consumo interno de usinas; inclui MMGD",
})
df["documento_complementar"] = "PDE 2036 - Caderno de Demanda de Energia"
df["url_documento_complementar"] = (
    "https://www.epe.gov.br/sites-pt/publicacoes-dados-abertos/publicacoes/"
    "PublicacoesArquivos/publicacao-982/topico-851/"
    "PDE%202036_Caderno%20de%20Demanda%20de%20Energia_v1_2026-10-27.pdf"
)
df["versao_documento_complementar"] = "arquivo v1_2026-10-27"
df["pagina_impressa_complementar"] = df["indicador"].map({"data_centers": 36, "eletricidade_total": 11})
# Data desta verificação, não a data desconhecida da consulta original.
df["data_verificacao_referencias"] = "2026-11-10"

# Validações
if df.duplicated(["ano", "indicador"]).any():
    raise ValueError("Indicadores duplicados.")

if df["valor_twh"].isna().any():
    raise ValueError("Valores ausentes.")

if not df["valor_twh"].gt(0).all():
    raise ValueError("Valores inválidos.")

# Salvar curadoria validada como execução concluída.
with Publicacao(BASE_DIR, "epe") as publicacao:
    relativo = "data/reference/epe_pde2036.csv"
    temporario = publicacao.caminho(relativo)
    df.to_csv(temporario, index=False, encoding="utf-8-sig")
    gravado = pd.read_csv(temporario)
    if len(gravado) != len(df) or gravado.duplicated(["ano", "indicador"]).any():
        raise ValueError("Curadoria EPE gravada é inconsistente.")
    try:
        pd.testing.assert_frame_equal(gravado, df, check_dtype=False, check_exact=True)
    except AssertionError as erro:
        raise ValueError("Curadoria EPE divergiu na gravação.") from erro
    publicacao.confirmar([relativo], {"registros": len(df), "origem": "curadoria_manual"})

print("Base EPE criada com sucesso!")
print(f"Registros: {len(df)}")

print("\nDados:")
print(df[[
    "ano",
    "indicador",
    "valor_twh",
    "natureza"
]].to_string(index=False))
