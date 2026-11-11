
"""Consulta exploratória do catálogo de geração; não realiza ingestão."""

import re
import requests

API_URL = (
    "https://dados.ons.org.br/api/3/action/"
    "package_show"
)

resposta = requests.get(
    API_URL,
    params={"id": "geracao-usina-2"},
    timeout=30
)

resposta.raise_for_status()
dados = resposta.json()

if not dados.get("success"):
    raise RuntimeError("Falha na consulta ao ONS.")

recursos = dados["result"]["resources"]

arquivos = []

for recurso in recursos:
    nome = recurso.get("name", "")
    formato = str(recurso.get("format") or "").upper()

    if formato != "PARQUET":
        continue

    correspondencia = re.search(
        r"(\d{4})-(0[1-9]|1[0-2])",
        nome
    )

    if correspondencia:
        arquivos.append({
            "nome": nome,
            "url": recurso["url"],
            "tamanho_bytes": recurso.get("size")
        })

arquivos.sort(key=lambda x: x["nome"])

print(f"Arquivos encontrados: {len(arquivos)}")

for arquivo in arquivos[-12:]:
    print(
        arquivo["nome"],
        "|",
        arquivo["url"]
    )
