"""Publicação versionada por pipeline; leitores coerentes consultam o ponteiro."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid


def sha256(arquivo):
    digest = hashlib.sha256()
    with Path(arquivo).open("rb") as entrada:
        for bloco in iter(lambda: entrada.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def _arquivo_atomico(destino, dados):
    destino.parent.mkdir(parents=True, exist_ok=True)
    nome = None
    try:
        with tempfile.NamedTemporaryFile(dir=destino.parent, delete=False) as arquivo:
            nome = Path(arquivo.name)
            arquivo.write(dados)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(nome, destino)
    finally:
        if nome is not None:
            nome.unlink(missing_ok=True)


def _json_atomico(destino, objeto):
    _arquivo_atomico(destino, (json.dumps(objeto, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _copiar_atomico(origem, destino):
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = None
    try:
        with tempfile.NamedTemporaryFile(dir=destino.parent, delete=False) as arquivo:
            temporario = Path(arquivo.name)
            with Path(origem).open("rb") as entrada:
                shutil.copyfileobj(entrada, arquivo, 1024 * 1024)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, destino)
    finally:
        if temporario is not None:
            temporario.unlink(missing_ok=True)


@contextmanager
def _bloqueio(pasta):
    """O SO libera o bloqueio quando o processo termina, inclusive por falha."""
    pasta.mkdir(parents=True, exist_ok=True)
    with (pasta / "lock").open("a+b") as arquivo:
        arquivo.seek(0, os.SEEK_END)
        if arquivo.tell() == 0:
            arquivo.write(b"0")
            arquivo.flush()
        arquivo.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(arquivo.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as erro:
                raise RuntimeError("Outra publicação do mesmo pipeline está em andamento.") from erro
        else:
            import fcntl
            try:
                fcntl.flock(arquivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as erro:
                raise RuntimeError("Outra publicação do mesmo pipeline está em andamento.") from erro
        try:
            yield
        finally:
            arquivo.seek(0)
            if os.name == "nt":
                msvcrt.locking(arquivo.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(arquivo, fcntl.LOCK_UN)


def _relativo(caminho):
    caminho = Path(caminho)
    if caminho.is_absolute() or ".." in caminho.parts or not caminho.parts or caminho.parts[0] != "data":
        raise ValueError("Caminho de publicação inválido.")
    if len(caminho.parts) < 3 or caminho.parts[1] not in ("silver", "gold", "reference"):
        raise ValueError("Publicação permitida apenas em Silver, Gold ou Reference.")
    return caminho


class Publicacao:
    """Uma publicação por domínio; arquivos fixos são projeções de compatibilidade."""

    def __init__(self, raiz, dominio):
        if not dominio or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in dominio):
            raise ValueError("Domínio de publicação inválido.")
        self.raiz = Path(raiz).resolve()
        self.pasta = self.raiz / "data" / ".publicacoes" / dominio
        self.ponteiro = self.pasta / "atual.json"
        self.pendente = self.pasta / "pendente.json"
        self.id = uuid.uuid4().hex
        self.versao = self.pasta / "execucoes" / self.id
        self._lock = None

    def __enter__(self):
        self._lock = _bloqueio(self.pasta)
        self._lock.__enter__()
        try:
            self.recuperar()
            self.versao.mkdir(parents=True)
        except BaseException:
            self._lock.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, tipo, valor, traceback):
        try:
            if tipo is not None:
                self.recuperar()
        finally:
            self._lock.__exit__(tipo, valor, traceback)

    def caminho(self, relativo):
        destino = self.versao / "arquivos" / _relativo(relativo)
        destino.parent.mkdir(parents=True, exist_ok=True)
        return destino

    def recuperar(self):
        if not self.pendente.exists():
            return
        transacao = json.loads(self.pendente.read_text(encoding="utf-8"))
        atual = json.loads(self.ponteiro.read_text(encoding="utf-8")) if self.ponteiro.exists() else {}
        backup = self.pasta / "backups" / transacao["id"]
        if atual.get("id") != transacao["id"]:
            for item in transacao["arquivos"]:
                relativo = _relativo(item["caminho"])
                destino = self.raiz / relativo
                if item["existia"]:
                    _copiar_atomico(backup / relativo, destino)
                else:
                    destino.unlink(missing_ok=True)
        self.pendente.unlink()
        shutil.rmtree(backup, ignore_errors=True)

    def confirmar(self, relativos, qualidade=None):
        relativos = [_relativo(item) for item in relativos]
        if not relativos or len(set(relativos)) != len(relativos):
            raise ValueError("Lista de arquivos vazia ou duplicada.")
        itens = []
        for relativo in relativos:
            caminho = self.versao / "arquivos" / relativo
            if not caminho.is_file():
                raise FileNotFoundError(f"Saída em staging ausente: {relativo}")
            if caminho.suffix == ".parquet":
                import pyarrow.parquet as pq
                parquet = pq.ParquetFile(caminho)
                try:
                    if parquet.metadata.num_rows == 0 or not parquet.schema_arrow.names:
                        raise ValueError(f"Parquet sem dados ou schema: {relativo}")
                    lidos = sum(len(lote) for lote in parquet.iter_batches(batch_size=65536))
                    if lidos != parquet.metadata.num_rows:
                        raise ValueError(f"Parquet incompleto: {relativo}")
                finally:
                    parquet.close()
            itens.append({"caminho": relativo.as_posix(), "bytes": caminho.stat().st_size,
                          "sha256": sha256(caminho)})
        # Normaliza tuplas e demais contêineres antes de comparar com o JSON anterior.
        qualidade = json.loads(json.dumps(qualidade or {}, ensure_ascii=False, allow_nan=False))
        manifesto = {"id": self.id, "concluida_em": datetime.now(timezone.utc).isoformat(),
                     "arquivos": itens, "qualidade": qualidade}
        _json_atomico(self.versao / "manifesto.json", manifesto)
        # Não cria versões redundantes quando tanto os bytes versionados quanto os
        # caminhos de compatibilidade ainda coincidem com a execução concluída.
        if self.ponteiro.exists():
            anterior = json.loads(self.ponteiro.read_text(encoding="utf-8"))
            manifest_path = self.pasta / "execucoes" / anterior["id"] / "manifesto.json"
            anterior_manifesto = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (anterior_manifesto["arquivos"] == itens and anterior_manifesto["qualidade"] == manifesto["qualidade"]
                    and all((self.raiz / item["caminho"]).is_file()
                            and sha256(self.raiz / item["caminho"]) == item["sha256"]
                            and (manifest_path.parent / "arquivos" / item["caminho"]).is_file()
                            and sha256(manifest_path.parent / "arquivos" / item["caminho"]) == item["sha256"]
                            for item in itens)):
                shutil.rmtree(self.versao)
                return anterior["id"]
        backup = self.pasta / "backups" / self.id
        operacoes = []
        for item in itens:
            relativo = Path(item["caminho"])
            destino = self.raiz / relativo
            existe = destino.is_file()
            if existe:
                _copiar_atomico(destino, backup / relativo)
            operacoes.append({"caminho": item["caminho"], "existia": existe})
        _json_atomico(self.pendente, {"id": self.id, "arquivos": operacoes})
        for item in itens:
            relativo = Path(item["caminho"])
            _copiar_atomico(self.versao / "arquivos" / relativo, self.raiz / relativo)
        _json_atomico(self.ponteiro, {"id": self.id})
        self.recuperar()
        return self.id


def ler_concluida(raiz, dominio):
    """Fixa uma versão e verifica seus bytes antes de entregá-la ao consumidor."""
    pub = Publicacao(raiz, dominio)
    ponteiro = json.loads(pub.ponteiro.read_text(encoding="utf-8"))
    identificador = ponteiro["id"]
    if len(identificador) != 32 or any(c not in "0123456789abcdef" for c in identificador):
        raise ValueError("Identificador de execução inválido.")
    versao = pub.pasta / "execucoes" / identificador
    manifesto = json.loads((versao / "manifesto.json").read_text(encoding="utf-8"))
    if manifesto["id"] != identificador:
        raise ValueError("Manifesto não corresponde ao ponteiro.")
    arquivos = {}
    for item in manifesto["arquivos"]:
        relativo = _relativo(item["caminho"])
        arquivo = versao / "arquivos" / relativo
        if arquivo.stat().st_size != item["bytes"] or sha256(arquivo) != item["sha256"]:
            raise ValueError(f"Integridade divergente: {relativo}")
        arquivos[item["caminho"]] = arquivo
    return manifesto, arquivos
