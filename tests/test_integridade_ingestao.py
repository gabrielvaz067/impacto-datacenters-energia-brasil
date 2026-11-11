"""Regressões de corrupção, recuperação e publicação em pastas temporárias."""

import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import requests

from src import extracao_aneel as aneel, extracao_geracao_ons as geracao
from src import integridade_ingestao as integridade


def http(corpo=b"", status=200):
    resposta = requests.Response()
    resposta.status_code = status
    resposta._content = corpo
    resposta._content_consumed = True
    resposta.headers = {"ETag": '"etag"', "Last-Modified": "Mon, 09 Nov 2026 22:00:00 GMT"}
    return resposta


def parquet(valor=1.0, mes=9):
    dados = {nome: ["texto", "texto"] for nome in integridade.COLUNAS_GERACAO[1:-1]}
    dados["din_instante"] = pa.array([f"2026-{mes:02d}-01T00:00:00", f"2026-{mes:02d}-01T01:00:00"]).cast(pa.timestamp("s"))
    dados["val_geracao"] = pa.array([valor, None], type=pa.float64())
    buffer = io.BytesIO()
    pq.write_table(pa.table(dados), buffer, compression=None, use_dictionary=False, write_page_checksum=True)
    return buffer.getvalue()


def csv_aneel(nome="Usina"):
    campos = sorted(integridade.COLUNAS_ANEEL)
    registro = {campo: "texto" for campo in campos}
    registro.update(CodCEG="UHE.TESTE.001", NomEmpreendimento=nome,
                    DscFaseUsina="Operação", DatGeracaoConjuntoDados="2026-11-09",
                    DatEntradaOperacao="", MdaPotenciaFiscalizadaKw="1000.5",
                    MdaPotenciaOutorgadaKw="1000,50")
    buffer = io.StringIO(newline="")
    escritor = csv.writer(buffer, delimiter=";", lineterminator="\n")
    escritor.writerow(campos)
    escritor.writerow([registro[c] for c in campos])
    escritor.writerow([registro[c] for c in campos])  # CEG redundante é aceito.
    return buffer.getvalue().encode("utf-8")


class IntegridadeTest(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory(prefix="integridade_")
        self.addCleanup(self.pasta.cleanup)
        self.raiz = Path(self.pasta.name)
        self.raw = self.raiz / "raw"
        self.raw.mkdir()
        self.estado = self.raiz / "controle.json"
        self.destino = self.raw / "2026/geracao_usina_2026_09.parquet"
        self.recurso = {"ano": 2026, "mes": 9, "url": "https://dados.ons.org.br/test.parquet"}

    def baixar(self, respostas, estado):
        with patch.object(geracao, "RAW_DIR", self.raw), patch.object(geracao, "STATE_FILE", self.estado), \
                patch.object(requests, "get", side_effect=respostas) as get:
            resultado = geracao.baixar_recurso(self.recurso, estado)
        return resultado, get

    def criar_cache(self):
        estado = {}
        self.baixar([http(parquet())], estado)
        return estado

    def test_parquet_valido_com_nulos_e_negativos(self):
        arquivo = self.raiz / "válido.parquet"
        for valor in (-2.0, 0.0):
            arquivo.write_bytes(parquet(valor))
            integridade.validar_parquet(arquivo, 2026, 9)

    def test_parquet_rejeita_ambos_infinitos(self):
        arquivo = self.raiz / "infinito.parquet"
        for valor in (float("inf"), float("-inf")):
            with self.subTest(valor=valor):
                arquivo.write_bytes(parquet(valor))
                with self.assertRaisesRegex(ValueError, "infinitos"):
                    integridade.validar_parquet(arquivo, 2026, 9)

    def test_infinito_em_lote_posterior_e_rejeitado(self):
        tabela = pq.read_table(io.BytesIO(parquet()))
        tabela = pa.concat_tables([tabela] * 32769)
        indice = tabela.schema.get_field_index("val_geracao")
        valores = pa.array([1.0] * 65537 + [float("inf")])
        tabela = tabela.set_column(indice, "val_geracao", valores)
        arquivo = self.raiz / "lotes.parquet"
        pq.write_table(tabela, arquivo)
        with self.assertRaisesRegex(ValueError, "infinitos"):
            integridade.validar_parquet(arquivo, 2026, 9)

    def test_parquet_falso_schema_errado_competencia_errada(self):
        arquivo = self.raiz / "incorreto.parquet"
        esquemaruim = io.BytesIO()
        pq.write_table(pa.table({"din_instante": ["data"], "val_geracao": [1]}), esquemaruim)
        for corpo in (b"PAR1nao_e_parquetPAR1", esquemaruim.getvalue(), parquet(mes=10)):
            with self.subTest(tamanho=len(corpo)):
                arquivo.write_bytes(corpo)
                with self.assertRaises(ValueError):
                    integridade.validar_parquet(arquivo, 2026, 9)

    def test_parquet_coluna_essencial_com_tipo_errado(self):
        tabela = pq.read_table(io.BytesIO(parquet()))
        indice = tabela.schema.get_field_index("val_geracao")
        tabela = tabela.set_column(indice, "val_geracao", pa.array(["1", "2"]))
        arquivo = self.raiz / "tipo-invalido.parquet"
        pq.write_table(tabela, arquivo)
        with self.assertRaisesRegex(ValueError, "val_geracao"):
            integridade.validar_parquet(arquivo, 2026, 9)

    def test_parquet_corrompido_no_conteudo_lido(self):
        arquivo = self.raiz / "corrompido.parquet"
        arquivo.write_bytes(parquet())
        info = pq.ParquetFile(arquivo).metadata.row_group(0).column(0)
        posicao = info.data_page_offset + 25
        dados = bytearray(arquivo.read_bytes())
        dados[posicao] ^= 1
        arquivo.write_bytes(dados)
        pq.ParquetFile(arquivo)  # Metadados continuam legíveis.
        with self.assertRaises(ValueError):
            integridade.validar_parquet(arquivo, 2026, 9)

    def test_csv_valido_duplicado_e_invalidos(self):
        arquivo = self.raiz / "aneel.csv"
        arquivo.write_bytes(csv_aneel())
        integridade.validar_csv_aneel(arquivo)
        invalidos = (b"<html>NomEmpreendimento</html>", b'{"error":"falha"}',
                     b"NomEmpreendimento;CodCEG\nTeste;1\n", csv_aneel()[:-12],
                     csv_aneel().replace(b"Usina", b"\xffsina"),
                     csv_aneel().replace(b"1000,50", b"invalido"),
                     csv_aneel().replace(b"2026-11-09", b"data-invalida"))
        for corpo in invalidos:
            with self.subTest(corpo=corpo[:30]):
                arquivo.write_bytes(corpo)
                with self.assertRaises(ValueError):
                    integridade.validar_csv_aneel(arquivo)

    def test_304_cache_valido_e_idempotencia(self):
        estado = self.criar_cache()
        antes = self.destino.read_bytes(), self.destino.stat().st_mtime_ns
        resultado, get = self.baixar([http(status=304)], estado)
        self.assertEqual(resultado, "sem_alteracao")
        self.assertIn("If-None-Match", get.call_args.kwargs["headers"])
        self.assertEqual(antes, (self.destino.read_bytes(), self.destino.stat().st_mtime_ns))

    def test_304_arquivo_ausente_recupera_sem_condicionais(self):
        estado = self.criar_cache()
        self.destino.unlink()
        resultado, get = self.baixar([http(status=304), http(parquet())], estado)
        self.assertEqual(resultado, "atualizado")
        self.assertEqual([c.kwargs["headers"] for c in get.call_args_list], [{}, {}])
        integridade.validar_parquet(self.destino, 2026, 9)

    def test_304_cache_alterado_durante_requisicao_recupera(self):
        estado = self.criar_cache()
        chamadas = []
        def resposta_race(url, **kwargs):
            chamadas.append(kwargs["headers"].copy())
            if len(chamadas) == 1:
                self.destino.write_bytes(b"x" * self.destino.stat().st_size)
                return http(status=304)
            return http(parquet())
        with patch.object(geracao, "RAW_DIR", self.raw), patch.object(geracao, "STATE_FILE", self.estado), \
                patch.object(requests, "get", side_effect=resposta_race):
            self.assertEqual(geracao.baixar_recurso(self.recurso, estado), "atualizado")
        self.assertIn("If-None-Match", chamadas[0])
        self.assertEqual(chamadas[1], {})
        self.assertEqual(integridade.sha256(self.destino), estado["2026-09"]["sha256"])

    def test_hash_divergente_mesmo_tamanho_forca_download(self):
        estado = self.criar_cache()
        dados = bytearray(self.destino.read_bytes())
        dados[80] ^= 1
        self.destino.write_bytes(dados)
        resultado, get = self.baixar([http(parquet())], estado)
        self.assertEqual(resultado, "atualizado")
        self.assertEqual(get.call_args.kwargs["headers"], {})

    def test_reinicio_com_arquivo_novo_e_controle_antigo_reconcilia(self):
        estado = self.criar_cache()
        self.destino.write_bytes(parquet(2.0))  # Simula interrupção entre promoção e controle.
        resultado, get = self.baixar([http(parquet(2.0))], estado)
        self.assertEqual(resultado, "atualizado")
        self.assertEqual(get.call_args.kwargs["headers"], {})
        self.assertEqual(integridade.sha256(self.destino), estado["2026-09"]["sha256"])

    def test_304_sem_cache_e_sem_recuperacao_falha(self):
        with self.assertRaisesRegex(ValueError, "304 sem cache"):
            self.baixar([http(status=304), http(status=304)], {})
        self.assertFalse(self.destino.exists())

    def test_download_invalido_preserva_ultima_versao_valida(self):
        estado = self.criar_cache()
        antes = self.destino.read_bytes(), self.estado.read_bytes()
        with self.assertRaises(ValueError):
            self.baixar([http(b"PAR1lixoPAR1")], estado)
        self.assertEqual(antes, (self.destino.read_bytes(), self.estado.read_bytes()))

    def test_download_interrompido_preserva_ultima_versao_valida(self):
        estado = self.criar_cache()
        antes = self.destino.read_bytes(), self.estado.read_bytes()
        resposta = http(parquet(2.0))
        def interromper(**kwargs):
            yield b"fragmento"
            raise requests.ConnectionError("conexão interrompida")
        resposta.iter_content = interromper
        with patch("src.rede.time.sleep"), self.assertRaises(requests.ConnectionError):
            self.baixar([resposta, resposta, resposta], estado)
        self.assertEqual(antes, (self.destino.read_bytes(), self.estado.read_bytes()))

    def test_falha_controle_apos_gravacao_restaura_dados_e_controle(self):
        estado = self.criar_cache()
        antes = self.destino.read_bytes(), self.estado.read_bytes(), json.dumps(estado, sort_keys=True)
        salvar_original = geracao.salvar_estado
        def falhar(novo):
            salvar_original(novo)
            raise OSError("falha após gravar controle")
        with patch.object(geracao, "salvar_estado", side_effect=falhar), self.assertRaises(OSError):
            self.baixar([http(parquet(2.0))], estado)
        self.assertEqual(antes, (self.destino.read_bytes(), self.estado.read_bytes(), json.dumps(estado, sort_keys=True)))

    def test_falha_na_restauracao_preserva_copias_de_recuperacao(self):
        estado = self.criar_cache()
        dados_anteriores = self.destino.read_bytes()
        substituir = Path.replace
        def recusar_restauracao(caminho, destino):
            if caminho.suffix == ".rollback" and Path(destino) == self.destino:
                raise OSError("restauração indisponível")
            return substituir(caminho, destino)
        with patch.object(geracao, "salvar_estado", side_effect=OSError("controle indisponível")), \
                patch.object(Path, "replace", recusar_restauracao), \
                self.assertRaisesRegex(RuntimeError, "cópias de recuperação mantidas"):
            self.baixar([http(parquet(2.0))], estado)
        backups = list(self.destino.parent.glob("*.rollback"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), dados_anteriores)

    def atualizar_aneel(self, respostas, recurso=None):
        with patch.object(aneel, "RAW_DIR", self.raw), patch.object(aneel, "ARQUIVO", self.raw / "siga.csv"), \
                patch.object(aneel, "STATE_FILE", self.estado), \
                patch.object(aneel, "consultar_recurso", return_value=recurso or {"url": "https://aneel.test/csv", "hash": "catalogo"}), \
                patch.object(requests, "get", side_effect=respostas) as get:
            aneel.atualizar()
        return get

    def test_aneel_migracao_legado_e_cache_verificado(self):
        destino = self.raw / "siga.csv"
        destino.write_bytes(csv_aneel())
        self.estado.write_text(json.dumps({"url": "https://aneel.test/csv", "hash": "catalogo",
                                           "metadado_legado": "preservado"}))
        get = self.atualizar_aneel([http(csv_aneel())])
        self.assertEqual(get.call_args.kwargs["headers"], {})
        estado = json.loads(self.estado.read_text(encoding="utf-8"))
        self.assertEqual(estado["sha256"], integridade.sha256(destino))
        self.assertEqual(estado["metadado_legado"], "preservado")
        get = self.atualizar_aneel([])
        get.assert_not_called()

    def test_aneel_cache_adulterado_mesmo_tamanho_e_recuperacao(self):
        self.atualizar_aneel([http(csv_aneel())])
        destino = self.raw / "siga.csv"
        destino.write_bytes(destino.read_bytes().replace(b"Usina", b"Outra"))
        get = self.atualizar_aneel([http(csv_aneel())])
        self.assertEqual(get.call_args.kwargs["headers"], {})
        self.assertEqual(destino.read_bytes(), csv_aneel())

    def test_aneel_304_valido_e_304_inesperado(self):
        self.atualizar_aneel([http(csv_aneel())])
        recurso = {"url": "https://aneel.test/csv", "hash": "nova-versao"}
        get = self.atualizar_aneel([http(status=304)], recurso)
        self.assertIn("If-None-Match", get.call_args.kwargs["headers"])
        (self.raw / "siga.csv").unlink()
        get = self.atualizar_aneel([http(status=304), http(csv_aneel())], recurso)
        self.assertEqual([c.kwargs["headers"] for c in get.call_args_list], [{}, {}])

    def test_aneel_304_com_cache_alterado_forca_recuperacao(self):
        self.atualizar_aneel([http(csv_aneel())])
        destino = self.raw / "siga.csv"
        chamadas = []
        def resposta_race(url, **kwargs):
            chamadas.append(kwargs["headers"].copy())
            if len(chamadas) == 1:
                destino.write_bytes(destino.read_bytes().replace(b"Usina", b"Outra"))
                return http(status=304)
            return http(csv_aneel())
        with patch.object(aneel, "RAW_DIR", self.raw), patch.object(aneel, "ARQUIVO", destino), \
                patch.object(aneel, "STATE_FILE", self.estado), \
                patch.object(aneel, "consultar_recurso", return_value={"url": "https://aneel.test/csv", "hash": "novo"}), \
                patch.object(requests, "get", side_effect=resposta_race):
            aneel.atualizar()
        self.assertIn("If-None-Match", chamadas[0])
        self.assertEqual(chamadas[1], {})
        self.assertEqual(destino.read_bytes(), csv_aneel())

    def test_aneel_rejeita_csv_invalido_sem_substituir(self):
        self.atualizar_aneel([http(csv_aneel())])
        destino = self.raw / "siga.csv"
        antes = destino.read_bytes(), self.estado.read_bytes()
        recurso = {"url": "https://aneel.test/csv", "hash": "revisao"}
        with self.assertRaises(ValueError):
            self.atualizar_aneel([http(b"<html>erro</html>")], recurso)
        self.assertEqual(antes, (destino.read_bytes(), self.estado.read_bytes()))

    def test_aneel_falha_controle_restaura_dados_e_controle(self):
        self.atualizar_aneel([http(csv_aneel())])
        destino = self.raw / "siga.csv"
        antes = destino.read_bytes(), self.estado.read_bytes()
        original = aneel.salvar_estado
        def falhar(novo):
            original(novo)
            raise OSError("controle falhou")
        with patch.object(aneel, "salvar_estado", side_effect=falhar), self.assertRaises(OSError):
            self.atualizar_aneel([http(csv_aneel("Nova"))], {"url": "https://aneel.test/csv", "hash": "novo"})
        self.assertEqual(antes, (destino.read_bytes(), self.estado.read_bytes()))


if __name__ == "__main__":
    unittest.main()
