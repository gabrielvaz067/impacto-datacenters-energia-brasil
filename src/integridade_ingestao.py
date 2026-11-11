"""Valida insumos operacionais e publica um arquivo com recuperação local."""

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path
import shutil
import tempfile

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


COLUNAS_GERACAO = [
    "din_instante", "id_subsistema", "nom_subsistema", "id_estado", "nom_estado",
    "cod_modalidadeoperacao", "nom_tipousina", "nom_tipocombustivel", "nom_usina",
    "id_ons", "ceg", "val_geracao",
]
COLUNAS_ANEEL = {
    "CodCEG", "IdeNucleoCEG", "NomEmpreendimento", "SigUFPrincipal", "SigTipoGeracao",
    "DscFaseUsina", "DscOrigemCombustivel", "DscFonteCombustivel",
    "MdaPotenciaFiscalizadaKw", "MdaPotenciaOutorgadaKw", "DatEntradaOperacao",
    "DatGeracaoConjuntoDados",
}


def sha256(caminho):
    with Path(caminho).open("rb") as arquivo:
        return hashlib.file_digest(arquivo, "sha256").hexdigest()


def validar_parquet(caminho, ano, mes):
    try:
        with Path(caminho).open("rb") as fonte:
            parquet = pq.ParquetFile(fonte, page_checksum_verification=True)
            schema = parquet.schema_arrow
            if len(schema.names) != len(set(schema.names)):
                raise ValueError("Colunas duplicadas no Parquet.")
            faltantes = set(COLUNAS_GERACAO) - set(schema.names)
            if faltantes:
                raise ValueError(f"Colunas ausentes no Parquet: {sorted(faltantes)}")
            if not pa.types.is_timestamp(schema.field("din_instante").type):
                raise ValueError("Tipo de din_instante incompatível.")
            valor = schema.field("val_geracao").type
            if not (pa.types.is_integer(valor) or pa.types.is_floating(valor) or pa.types.is_decimal(valor)):
                raise ValueError("Tipo de val_geracao incompatível.")
            for nome in COLUNAS_GERACAO[1:-1]:
                tipo = schema.field(nome).type
                if not (pa.types.is_string(tipo) or pa.types.is_large_string(tipo)):
                    raise ValueError(f"Tipo incompatível: {nome}")
            if parquet.metadata.num_rows <= 0 or parquet.metadata.num_row_groups <= 0:
                raise ValueError("Parquet sem registros.")
            linhas = 0
            for lote in parquet.iter_batches(batch_size=65536, columns=COLUNAS_GERACAO):
                lote.validate(full=True)
                datas = lote.column(0)
                if (datas.null_count or not pc.all(pc.equal(pc.year(datas), ano)).as_py()
                        or not pc.all(pc.equal(pc.month(datas), mes)).as_py()):
                    raise ValueError(f"Datas incompatíveis com {ano:04d}-{mes:02d}.")
                if pa.types.is_floating(valor) and pc.any(pc.is_inf(lote.column(len(COLUNAS_GERACAO) - 1))).as_py():
                    raise ValueError("Valores infinitos em val_geracao.")
                linhas += lote.num_rows
            if linhas != parquet.metadata.num_rows:
                raise ValueError("Contagem de linhas diverge dos metadados Parquet.")
    except (pa.ArrowException, OSError) as erro:
        raise ValueError(f"Parquet inválido ou ilegível: {erro}") from erro


def validar_csv_aneel(caminho):
    try:
        with Path(caminho).open("r", encoding="utf-8-sig", newline="") as arquivo:
            leitor = csv.reader(arquivo, delimiter=";", strict=True)
            cabecalho = next(leitor, None)
            if not cabecalho or len(cabecalho) != len(set(cabecalho)):
                raise ValueError("Cabeçalho CSV inválido.")
            faltantes = COLUNAS_ANEEL - set(cabecalho)
            if faltantes:
                raise ValueError(f"Colunas ANEEL ausentes: {sorted(faltantes)}")
            indices = {coluna: cabecalho.index(coluna) for coluna in COLUNAS_ANEEL}
            quantidade = 0
            for linha in leitor:
                if len(linha) != len(cabecalho):
                    raise ValueError(f"Linha CSV incompleta: {leitor.line_num}")
                for nome in ("CodCEG", "NomEmpreendimento", "DscFaseUsina", "DatGeracaoConjuntoDados"):
                    if not linha[indices[nome]].strip():
                        raise ValueError(f"Campo obrigatório vazio: {nome}")
                datetime.fromisoformat(linha[indices["DatGeracaoConjuntoDados"]].strip())
                fiscal = Decimal(linha[indices["MdaPotenciaFiscalizadaKw"]].strip())
                outorgada = Decimal(linha[indices["MdaPotenciaOutorgadaKw"]].strip().replace(".", "").replace(",", "."))
                if not fiscal.is_finite() or not outorgada.is_finite() or fiscal < 0 or outorgada < 0:
                    raise ValueError("Potência inválida no CSV ANEEL.")
                quantidade += 1
            if not quantidade:
                raise ValueError("CSV ANEEL sem empreendimentos.")
        # Uma linha cortada no fim pode manter o número de delimitadores.
        with Path(caminho).open("rb") as arquivo:
            arquivo.seek(-1, 2)
            if arquivo.read(1) not in (b"\n", b"\r"):
                raise ValueError("CSV sem término de linha: possível truncamento.")
    except (OSError, UnicodeError, csv.Error, InvalidOperation) as erro:
        raise ValueError(f"CSV ANEEL inválido ou ilegível: {erro}") from erro


def cache_valido(caminho, info, validar, exigir_hash=True):
    caminho = Path(caminho)
    if not caminho.is_file():
        return False
    if exigir_hash and not info.get("sha256"):
        return False
    try:
        if info.get("tamanho_bytes") is not None and caminho.stat().st_size != info["tamanho_bytes"]:
            return False
        if info.get("sha256") and sha256(caminho) != info["sha256"]:
            return False
        validar(caminho)
        return True
    except (OSError, ValueError, pa.ArrowException):
        return False


def publicar(temporario, destino, salvar_controle, caminho_controle):
    """Em falha de controle restaura bytes anteriores; após interrupção o hash força reconciliação."""
    destino = Path(destino)
    anterior = None
    controle = Path(caminho_controle)
    backup_controle = None
    controle_existia = controle.exists()
    preservar_backups = False
    try:
        if destino.exists():
            with tempfile.NamedTemporaryFile(dir=destino.parent, suffix=".rollback", delete=False) as arquivo:
                anterior = Path(arquivo.name)
            shutil.copyfile(destino, anterior)
        if controle_existia:
            with tempfile.NamedTemporaryFile(dir=controle.parent, suffix=".rollback", delete=False) as arquivo:
                backup_controle = Path(arquivo.name)
            shutil.copyfile(controle, backup_controle)
        Path(temporario).replace(destino)
        try:
            salvar_controle()
        except Exception:
            try:
                if anterior is not None:
                    anterior.replace(destino)
                else:
                    destino.unlink(missing_ok=True)
                if backup_controle is not None:
                    backup_controle.replace(controle)
                elif not controle_existia:
                    controle.unlink(missing_ok=True)
            except Exception as erro:
                preservar_backups = True
                raise RuntimeError(
                    f"Falha ao restaurar arquivos; cópias de recuperação mantidas: "
                    f"dados={anterior}, controle={backup_controle}"
                ) from erro
            raise
    finally:
        if anterior is not None and not preservar_backups:
            anterior.unlink(missing_ok=True)
        if backup_controle is not None and not preservar_backups:
            backup_controle.unlink(missing_ok=True)
