"""Testes offline: respostas HTTP controladas e dados escritos apenas em temporários."""

from contextlib import redirect_stdout
import importlib.util
import io
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests


RAIZ = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("extracao_ons", RAIZ / "src/extracao_ons.py")
extracao = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extracao)
HOST = "https://ons-aws-prod-opendata.s3.amazonaws.com/dataset/carga_energia_di/"


def url_ano(ano):
    return f"{HOST}CARGA_ENERGIA_{ano}.csv"


def resposta(corpo=b"", status=200, etag='"versao-1"'):
    http = requests.Response()
    http.status_code = status
    http._content = corpo
    http._content_consumed = True
    http.headers = {"ETag": etag, "Last-Modified": "Sun, 08 Nov 2026 22:00:38 GMT"}
    return http


def catalogo(anos):
    recursos = [{"name": f"Carga_Energia-{ano}", "format": "CSV", "url": url_ano(ano)}
                for ano in anos]
    return resposta(json.dumps({"success": True, "result": {"resources": recursos}}).encode())


def csv_sintetico():
    linhas = [";".join(extracao.COLUNAS)]
    for dia in ("2026-01-01", "2026-01-02"):
        for indice, (sigla, nome) in enumerate([
            ("N", "Norte"), ("NE", "Nordeste"), ("S", "Sul"), ("SE", "Sudeste/Centro-Oeste")
        ], start=1):
            linhas.append(f"{sigla};{nome};{dia};{indice * 1000}.5")
    return ("\n".join(linhas) + "\n").encode()


class ExtracaoTest(unittest.TestCase):
    def setUp(self):
        self.temporario = tempfile.TemporaryDirectory(prefix="teste_ons_")
        self.addCleanup(self.temporario.cleanup)
        self.raiz = Path(self.temporario.name)
        self.dados = self.raiz / "data"
        for pasta in ("raw", "processed"):
            (self.dados / pasta).mkdir(parents=True)
        self.raw = self.dados / "raw/CARGA_ENERGIA_2026.csv"
        self.processed = self.dados / "processed/carga_energia_2026.parquet"
        self.controle = self.dados / "controle_ons.json"
        self.estado = {}

    def atualizar(self, http):
        sessao = Mock()
        sessao.get.return_value = http
        resultado = extracao.atualizar_ano(sessao, 2026, url_ano(2026), self.dados, self.estado)
        return resultado, sessao

    def snapshot(self):
        return {str(p.relative_to(self.dados)): p.read_bytes()
                for p in self.dados.rglob("*") if p.is_file()}

    def test_catalogo_seleciona_anos_e_rejeita_ausencia_ambiguidade(self):
        sessao = Mock()
        sessao.get.return_value = catalogo([2024, 2025, 2026])
        self.assertEqual(extracao.consultar_recursos(sessao, [2026]), {2026: url_ano(2026)})
        self.assertEqual(sessao.get.call_args.kwargs["params"], {"id": "carga-energia"})
        with self.assertRaisesRegex(ValueError, "Anos sem CSV"):
            extracao.consultar_recursos(sessao, [2023])
        dados = catalogo([2026, 2026]).json()
        dados["result"]["resources"][1]["url"] += "?outra_versao=1"
        sessao.get.return_value = resposta(json.dumps(dados).encode())
        with self.assertRaisesRegex(ValueError, "Mais de um recurso"):
            extracao.consultar_recursos(sessao, [2026])

    def test_catalogo_invalido_e_urls_nao_oficiais(self):
        for dados in ({"success": False}, {"success": True, "result": {}}, []):
            with self.subTest(dados=dados):
                sessao = Mock()
                sessao.get.return_value = resposta(json.dumps(dados).encode())
                with self.assertRaises(ValueError):
                    extracao.consultar_recursos(sessao, [2026])
        for url in ("http://dados.ons.org.br/a", "https://example.com/a",
                    "https://dados.ons.org.br.evil.test/a", "https://user@dados.ons.org.br/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                extracao.validar_url(url)

    def test_primeira_carga_preserva_bytes_schema_chave_unidade(self):
        corpo = b"\xef\xbb\xbf" + csv_sintetico()
        resultado, _ = self.atualizar(resposta(corpo))
        self.assertEqual(resultado, "atualizado")
        self.assertEqual(self.raw.read_bytes(), corpo)
        df = pd.read_parquet(self.processed)
        self.assertEqual(list(df.columns), extracao.COLUNAS)
        self.assertEqual(len(df), 8)
        self.assertFalse(df.duplicated(["din_instante", "id_subsistema"]).any())
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(df["din_instante"]))
        self.assertEqual(str(df["val_cargaenergiamwmed"].dtype), "float64")
        self.assertEqual(df["val_cargaenergiamwmed"].sum(), 20004.0)
        self.assertEqual(self.estado["2026"]["sha256"], extracao.sha256(self.raw))

    def test_reexecucao_304_nao_regrava_dados_e_recupera_processed(self):
        self.atualizar(resposta(csv_sintetico()))
        antes = [(p.read_bytes(), p.stat().st_mtime_ns) for p in (self.raw, self.processed)]
        resultado, sessao = self.atualizar(resposta(status=304))
        self.assertEqual(resultado, "sem_alteracao")
        self.assertEqual(sessao.get.call_args.kwargs["headers"]["If-None-Match"], '"versao-1"')
        self.assertEqual(antes, [(p.read_bytes(), p.stat().st_mtime_ns)
                                for p in (self.raw, self.processed)])
        self.processed.unlink()
        self.atualizar(resposta(status=304))
        self.assertEqual(len(pd.read_parquet(self.processed)), 8)

    def test_200_identico_e_revisao_substituem_sem_duplicar(self):
        self.atualizar(resposta(csv_sintetico()))
        instante = self.raw.stat().st_mtime_ns
        self.assertEqual(self.atualizar(resposta(csv_sintetico()))[0], "sem_alteracao")
        self.assertEqual(self.raw.stat().st_mtime_ns, instante)
        revisado = csv_sintetico().replace(b"1000.5", b"1001.5", 1)
        self.assertEqual(self.atualizar(resposta(revisado, etag='"versao-2"'))[0], "atualizado")
        self.assertEqual(len(pd.read_parquet(self.processed)), 8)
        self.assertEqual(pd.read_parquet(self.processed)["val_cargaenergiamwmed"].sum(), 20005.0)
        self.assertEqual(self.estado["2026"]["etag"], '"versao-2"')

    def test_controle_legado_e_raw_modificado_localmente(self):
        self.atualizar(resposta(csv_sintetico()))
        del self.estado["2026"]["sha256"]
        _, sessao = self.atualizar(resposta(csv_sintetico()))
        self.assertEqual(sessao.get.call_args.kwargs["headers"], {})
        self.assertIn("sha256", self.estado["2026"])
        self.raw.write_bytes(csv_sintetico().replace(b"1000.5", b"1001.5", 1))
        _, sessao = self.atualizar(resposta(csv_sintetico()))
        self.assertEqual(sessao.get.call_args.kwargs["headers"], {})
        self.assertEqual(self.raw.read_bytes(), csv_sintetico())

    def test_controle_corrompido_nao_e_descartado(self):
        self.controle.write_text("{invalido", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Controle inv"):
            extracao.carregar_estado(self.controle)
        self.assertEqual(self.controle.read_text(), "{invalido")

    def test_304_sem_cache_e_redirecionamento_falham(self):
        for status in (304, 302, 404, 503):
            with self.subTest(status=status), self.assertRaises((ValueError, requests.HTTPError)):
                self.atualizar(resposta(status=status))
        self.assertEqual(self.snapshot(), {})

    def test_csv_inconsistente_preserva_estado_e_arquivos(self):
        self.atualizar(resposta(csv_sintetico()))
        antes = self.snapshot()
        original = csv_sintetico()
        linhas = original.splitlines(keepends=True)
        invalidos = {
            "vazio": b"", "html": b"<html>erro</html>",
            "duplicado": original + linhas[1],
            "subsistema_ausente": b"".join(linhas[:-1]),
            "cobertura_regrediu": b"".join(linhas[:5]),
            "nome_inconsistente": original.replace(b"N;Norte;2026-01-02", b"N;Outro;2026-01-02"),
            "data_ausente": original.replace(b"2026-01-02", b"2026-01-03"),
            "outro_ano": original.replace(b"2026-01-02", b"2025-01-02"),
            "horario": original.replace(b"2026-01-02", b"2026-01-02 12:00:00"),
            "negativo": original.replace(b"1000.5", b"-1"),
            "infinito": original.replace(b"1000.5", b"inf"),
            "nulo": original.replace(b"1000.5", b""),
            "coluna_ausente": original.replace(b"val_cargaenergiamwmed", b"outra"),
        }
        for caso, corpo in invalidos.items():
            with self.subTest(caso=caso), self.assertRaises(ValueError):
                self.atualizar(resposta(corpo))
            self.assertEqual(self.snapshot(), antes)

    def test_falha_de_rede_e_download_interrompido_preservam_dados(self):
        self.atualizar(resposta(csv_sintetico()))
        antes = self.snapshot()
        sessao = Mock()
        sessao.get.side_effect = requests.Timeout("timeout controlado")
        with self.assertRaises(requests.Timeout):
            extracao.atualizar_ano(sessao, 2026, url_ano(2026), self.dados, self.estado)
        http = resposta(csv_sintetico())

        def interromper(**kwargs):
            yield b"csv parcial"
            raise requests.ConnectionError("conexao interrompida")

        http.iter_content = interromper
        with self.assertRaises(requests.ConnectionError):
            self.atualizar(http)
        self.assertEqual(self.snapshot(), antes)

    def test_falha_parquet_nao_promove_raw(self):
        self.atualizar(resposta(csv_sintetico()))
        antes = self.snapshot()
        with patch.object(pd.DataFrame, "to_parquet", side_effect=OSError("disco indisponivel")):
            with self.assertRaises(OSError):
                self.atualizar(resposta(csv_sintetico().replace(b"1000.5", b"1001.5")))
        self.assertEqual(self.snapshot(), antes)

    def test_controle_falha_apos_gravacao_reverte_raw_processed_e_estado(self):
        self.atualizar(resposta(csv_sintetico()))
        antes = self.snapshot()
        salvar_original = extracao.salvar_estado
        def falhar(estado, destino):
            salvar_original(estado, destino)
            raise OSError("falha de controle")
        with patch.object(extracao, "salvar_estado", side_effect=falhar), self.assertRaises(OSError):
            self.atualizar(resposta(csv_sintetico().replace(b"1000.5", b"1001.5")))
        self.assertEqual(self.snapshot(), antes)

    def test_304_sem_cache_reexecuta_download_incondicional(self):
        sessao = Mock()
        sessao.get.side_effect = [resposta(status=304), resposta(csv_sintetico())]
        resultado = extracao.atualizar_ano(sessao, 2026, url_ano(2026), self.dados, self.estado)
        self.assertEqual(resultado, "atualizado")
        self.assertEqual([c.kwargs["headers"] for c in sessao.get.call_args_list], [{}, {}])

    def executar_cli_controlada(self, corpos, status=200):
        anos = sorted(corpos)
        respostas = [catalogo(anos)] + [resposta(corpos[a], status=status) for a in anos]
        argv = ["src/extracao_ons.py", "--anos", *map(str, anos), "--data-dir", str(self.dados)]
        with patch.object(sys, "argv", argv), patch.object(requests.Session, "get", side_effect=respostas):
            with redirect_stdout(io.StringIO()):
                extracao.main()

    def transformar(self):
        (self.raiz / "src").mkdir(exist_ok=True)
        shutil.copy2(RAIZ / "src/transformacao_ons.py", self.raiz / "src")
        shutil.copy2(RAIZ / "src/publicacao.py", self.raiz / "src")
        resultado = subprocess.run([sys.executable, "-B", "src/transformacao_ons.py"],
                                   cwd=self.raiz, capture_output=True, text=True)
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
        return (pd.read_parquet(self.dados / "silver/carga_energia.parquet"),
                pd.read_parquet(self.dados / "gold/carga_mensal.parquet"))

    def test_pipeline_sintetico_com_mes_incompleto_e_cli_da_raiz(self):
        resultado = subprocess.run([sys.executable, "-B", "src/extracao_ons.py", "--help"],
                                   cwd=RAIZ, capture_output=True, text=True)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.executar_cli_controlada({2026: csv_sintetico()})
        silver, gold = self.transformar()
        self.assertEqual(len(silver), 8)
        self.assertEqual(len(gold), 4)
        self.assertTrue(gold["dias_observados"].eq(2).all())
        self.assertFalse(gold["mes_completo"].any())
        self.assertEqual(silver["energia_mwh"].sum(), 20004.0 * 24)
        self.assertEqual(gold["energia_gwh"].sum(), 20004.0 * 24 / 1000)
        self.executar_cli_controlada({2026: csv_sintetico()}, status=304)
        silver2, gold2 = self.transformar()
        pd.testing.assert_frame_equal(silver, silver2)
        pd.testing.assert_frame_equal(gold, gold2)

    @unittest.skipUnless(os.environ.get("EXECUTAR_REGRESSAO_HISTORICA") == "1",
                         "Regressão histórica opcional: consulte o README")
    def test_regressao_snapshot_auditado_2024_2026(self):
        # Estes valores identificam o snapshot auditado; revisoes da fonte exigem
        # comparar as versoes antes de atualizar as expectativas deste teste.
        manifesto = json.loads((RAIZ / "edicoes/2026-11-09.json").read_text(encoding="utf-8"))
        hashes = {item["caminho"]: item["sha256"] for item in manifesto["arquivos"]}
        for ano in (2024, 2025, 2026):
            relativo = f"data/raw/CARGA_ENERGIA_{ano}.csv"
            arquivo = RAIZ / relativo
            self.assertTrue(arquivo.is_file(), f"Snapshot histórico ausente: {relativo}")
            self.assertEqual(hashlib.sha256(arquivo.read_bytes()).hexdigest(), hashes[relativo],
                             f"CSV não corresponde ao manifesto histórico: {relativo}")
        corpos = {a: (RAIZ / f"data/raw/CARGA_ENERGIA_{a}.csv").read_bytes()
                  for a in (2024, 2025, 2026)}
        self.executar_cli_controlada(corpos)
        silver, gold = self.transformar()
        self.assertEqual(len(silver), 4164)
        self.assertEqual(len(gold), 140)
        self.assertEqual(set(silver["id_subsistema"]), {"N", "NE", "S", "SE"})
        self.assertFalse(silver.duplicated(["data", "id_subsistema"]).any())
        self.assertFalse(gold.duplicated(["ano", "mes", "id_subsistema"]).any())
        self.assertEqual(int(gold["mes_completo"].sum()), 136)
        parcial = gold[(gold["ano"] == 2026) & (gold["mes"] == 11)]
        self.assertEqual(len(parcial), 4)
        self.assertTrue(parcial["dias_observados"].eq(6).all())
        self.assertFalse(parcial["mes_completo"].any())
        medias, dias = {}, {}
        for ano in corpos:
            periodo = silver[(silver["ano"] == ano) & (silver["mes"] <= 10)]
            diario = periodo.groupby("data")["carga_mwmed"].sum()
            dias[ano] = len(diario)
            medias[ano] = diario.mean()
            self.assertAlmostEqual(periodo["energia_mwh"].sum() / (len(diario) * 24), medias[ano])
            meses = gold[(gold["ano"] == ano) & (gold["mes"] <= 10)]
            self.assertAlmostEqual(meses["energia_gwh"].sum(), periodo["energia_mwh"].sum() / 1000)
            original = RAIZ / f"data/processed/carga_energia_{ano}.parquet"
            if original.exists():
                pd.testing.assert_frame_equal(pd.read_parquet(original),
                                              pd.read_parquet(self.dados / original.relative_to(RAIZ / "data")))
        self.assertEqual(dias, {2024: 305, 2025: 304, 2026: 304})
        self.assertAlmostEqual(medias[2026] / 1000, 80.80047594, places=8)
        self.assertAlmostEqual((medias[2025] / medias[2024] - 1) * 100, 0.7108738293)
        self.assertAlmostEqual((medias[2026] / medias[2025] - 1) * 100, 1.9219441904)
        for relativo, reconstruido in (("silver/carga_energia.parquet", silver),
                                       ("gold/carga_mensal.parquet", gold)):
            original = RAIZ / "data" / relativo
            if original.exists():
                pd.testing.assert_frame_equal(pd.read_parquet(original), reconstruido)
        self.executar_cli_controlada(corpos, status=304)
        silver2, gold2 = self.transformar()
        pd.testing.assert_frame_equal(silver, silver2)
        pd.testing.assert_frame_equal(gold, gold2)


if __name__ == "__main__":
    unittest.main()
