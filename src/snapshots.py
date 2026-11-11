"""Armazenamento local por SHA-256. Sem rede, links físicos ou sobrescrita."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import zipfile

MARCADOR = ".snapshot-store.json"
MANIFESTO_LOCAL = "manifesto_edicao.json"


def agora():
    return datetime.now(timezone.utc).isoformat()


def sha256(arquivo):
    with Path(arquivo).open("rb") as entrada:
        return hashlib.file_digest(entrada, "sha256").hexdigest()


def caminho_seguro(raiz, relativo):
    if not isinstance(relativo, str) or not relativo or "\\" in relativo or ":" in relativo:
        raise ValueError("Caminho relativo inválido no manifesto.")
    partes = PurePosixPath(relativo)
    if partes.is_absolute() or any(p in (".", "..") for p in relativo.split("/")):
        raise ValueError("Caminho fora da edição.")
    raiz = Path(raiz).resolve()
    destino = (raiz / relativo).resolve()
    if not destino.is_relative_to(raiz) or destino == raiz:
        raise ValueError("Caminho fora da edição.")
    return destino


def proteger_area_operacional(pasta):
    """Extratores não podem atualizar o repositório de objetos nem uma reprodução."""
    pasta = Path(pasta).resolve()
    for pai in (pasta, *pasta.parents):
        if (pai / MARCADOR).exists() or (pai / MANIFESTO_LOCAL).exists():
            raise ValueError("Área histórica protegida. Use outra pasta para atualização operacional.")


def validar_manifesto(manifesto):
    if not isinstance(manifesto, dict) or manifesto.get("versao_formato") != 1:
        raise ValueError("Versão de manifesto não suportada.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", str(manifesto.get("edicao", ""))):
        raise ValueError("Identificador de edição inválido.")
    datetime.fromisoformat(manifesto["preservado_em"])
    if not isinstance(manifesto.get("configuracao"), dict):
        raise ValueError("Configuração de edição ausente.")
    arquivos = manifesto.get("arquivos")
    if not isinstance(arquivos, list) or not arquivos:
        raise ValueError("Manifesto sem arquivos.")
    vistos = set()
    obrigatorios = {"tipo", "fonte", "dataset", "recurso", "referencia", "data_aquisicao",
                    "situacao_aquisicao", "caminho", "tamanho_bytes", "sha256",
                    "metadados_origem_registrados"}
    for item in arquivos:
        if not isinstance(item, dict) or not obrigatorios.issubset(item):
            raise ValueError("Registro incompleto no manifesto.")
        caminho_seguro(Path.cwd(), item["caminho"])
        chave = item["caminho"].casefold()
        if chave in vistos:
            raise ValueError("Caminho duplicado no manifesto.")
        vistos.add(chave)
        if not re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"])):
            raise ValueError("SHA-256 inválido.")
        if type(item["tamanho_bytes"]) is not int or item["tamanho_bytes"] < 0:
            raise ValueError("Tamanho inválido.")
        for campo in ("tipo", "fonte", "dataset", "recurso", "referencia", "situacao_aquisicao"):
            if not isinstance(item[campo], str) or not item[campo].strip():
                raise ValueError(f"Campo obrigatório inválido: {campo}")
        if item["data_aquisicao"] is not None:
            if datetime.fromisoformat(item["data_aquisicao"]).tzinfo is None:
                raise ValueError("Data de aquisição sem fuso horário.")
        elif item["situacao_aquisicao"] == "registrada":
            raise ValueError("Aquisição declarada sem data.")
        if not isinstance(item["metadados_origem_registrados"], dict):
            raise ValueError("Metadados de origem inválidos.")
    return manifesto


def ler_manifesto(arquivo):
    return validar_manifesto(json.loads(Path(arquivo).read_text(encoding="utf-8")))


def conferir(arquivo, item):
    arquivo = Path(arquivo)
    if not arquivo.is_file():
        raise FileNotFoundError(f"Snapshot solicitado ausente: {item['caminho']}")
    if arquivo.stat().st_size != item["tamanho_bytes"] or sha256(arquivo) != item["sha256"]:
        raise ValueError(f"Integridade divergente (tamanho/SHA-256): {item['caminho']}")


def objeto(armazenamento, item):
    return caminho_seguro(armazenamento, f"sha256/{item['sha256']}")


def guardar(arquivo, armazenamento, item):
    conferir(arquivo, item)
    armazenamento = Path(armazenamento)
    armazenamento.mkdir(parents=True, exist_ok=True)
    marcador = armazenamento / MARCADOR
    if not marcador.exists():
        with marcador.open("x", encoding="utf-8") as saida:
            json.dump({"versao": 1, "politica": "sem_sobrescrita"}, saida)
    destino = objeto(armazenamento, item)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        conferir(destino, item)
    else:
        # Cópia independente: alterar o Raw nunca altera o objeto preservado.
        with Path(arquivo).open("rb") as entrada, destino.open("xb") as saida:
            shutil.copyfileobj(entrada, saida)
        conferir(destino, item)


def verificar(manifesto, armazenamento):
    validar_manifesto(manifesto)
    for item in manifesto["arquivos"]:
        conferir(objeto(armazenamento, item), item)


def publicar_manifesto(manifesto, destino):
    validar_manifesto(manifesto)
    destino = Path(destino)
    if destino.exists():
        anterior = ler_manifesto(destino)
        # A data de preservação permanece sendo a da primeira execução.
        proposto = {**manifesto, "preservado_em": anterior["preservado_em"]}
        if anterior != proposto:
            raise FileExistsError("Edição já preservada com outro conteúdo/metadado. Use um novo identificador.")
        return anterior
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("x", encoding="utf-8") as saida:
        json.dump(manifesto, saida, indent=2, ensure_ascii=False)
        saida.write("\n")
    return manifesto


def exportar(manifesto_path, armazenamento, pacote):
    manifesto = ler_manifesto(manifesto_path)
    verificar(manifesto, armazenamento)
    with zipfile.ZipFile(pacote, "x", compression=zipfile.ZIP_DEFLATED) as arquivo:
        arquivo.write(manifesto_path, "manifesto.json")
        for digest, item in {a["sha256"]: a for a in manifesto["arquivos"]}.items():
            arquivo.write(objeto(armazenamento, item), f"sha256/{digest}")


def importar(manifesto_path, armazenamento, pacote):
    """O manifesto confiável é fornecido separadamente; nunca extrai caminhos do ZIP."""
    manifesto = ler_manifesto(manifesto_path)
    import tempfile

    with zipfile.ZipFile(pacote) as arquivo, tempfile.TemporaryDirectory() as temporario:
        nomes = arquivo.namelist()
        if len(nomes) != len(set(nomes)):
            raise ValueError("Pacote com entradas duplicadas.")
        if arquivo.read("manifesto.json") != Path(manifesto_path).read_bytes():
            raise ValueError("Manifesto do pacote difere do manifesto fornecido.")
        # Primeiro valida o pacote inteiro; depois insere objetos novos.
        unicos = {a["sha256"]: a for a in manifesto["arquivos"]}
        for digest, item in unicos.items():
            nome = f"sha256/{digest}"
            if arquivo.getinfo(nome).file_size != item["tamanho_bytes"]:
                raise ValueError("Tamanho do objeto no pacote divergente.")
            destino = Path(temporario) / digest
            with arquivo.open(nome) as entrada, destino.open("xb") as saida:
                shutil.copyfileobj(entrada, saida)
            conferir(destino, item)
        for digest, item in unicos.items():
            guardar(Path(temporario) / digest, armazenamento, item)
    verificar(manifesto, armazenamento)


def restaurar_arquivo(manifesto, armazenamento, caminho, destino):
    itens = [a for a in manifesto["arquivos"] if a["caminho"] == caminho]
    if len(itens) != 1:
        raise FileNotFoundError(f"Snapshot solicitado ausente do manifesto: {caminho}")
    item = itens[0]
    origem = objeto(armazenamento, item)
    conferir(origem, item)
    destino = Path(destino)
    if destino.exists():
        conferir(destino, item)
        return
    destino.parent.mkdir(parents=True, exist_ok=True)
    with origem.open("rb") as entrada, destino.open("xb") as saida:
        shutil.copyfileobj(entrada, saida)
    conferir(destino, item)
