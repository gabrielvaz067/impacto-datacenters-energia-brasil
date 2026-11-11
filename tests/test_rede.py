"""Tentativas e downloads simulados, sem acesso à rede."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from src import rede, extracao_ons, extracao_aneel, extracao_geracao_ons
from test_extracao_ons import csv_sintetico, resposta, url_ano
from test_integridade_ingestao import http, parquet, csv_aneel


def erro_http(status, retry_after=None):
    resposta_http = requests.Response()
    resposta_http.status_code = status
    resposta_http.url = "https://exemplo.test/recurso"
    if retry_after is not None:
        resposta_http.headers["Retry-After"] = retry_after
    try:
        resposta_http.raise_for_status()
    except requests.HTTPError as erro:
        return erro
    raise AssertionError("Status não gera erro")


class PoliticaRedeTest(unittest.TestCase):
    def _sequencia(self, efeitos):
        operacao = Mock(side_effect=efeitos)
        with patch.object(rede.time, "sleep") as espera:
            resultado = rede.repetir_transitorios(operacao)()
        return resultado, operacao, espera

    def test_erro_temporario_e_http_503_recuperam(self):
        resultado, operacao, espera = self._sequencia([
            requests.ConnectionError("temporaria"), erro_http(503), "ok"])
        self.assertEqual(resultado, "ok")
        self.assertEqual(operacao.call_count, 3)
        self.assertEqual([c.args[0] for c in espera.call_args_list], [0.5, 1.0])

    def test_retry_after_e_respeitado(self):
        resultado, operacao, espera = self._sequencia([erro_http(429, "4"), "ok"])
        self.assertEqual(resultado, "ok")
        self.assertEqual(operacao.call_count, 2)
        espera.assert_called_once_with(4.0)
        agora = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(rede.espera_retry_after("Tue, 10 Nov 2026 12:00:05 GMT", agora), 5)

    def test_retry_after_acima_do_limite_nao_tenta_cedo(self):
        operacao = Mock(side_effect=erro_http(429, "60"))
        with patch.object(rede.time, "sleep") as espera, self.assertRaises(requests.HTTPError):
            rede.repetir_transitorios(operacao)()
        self.assertEqual(operacao.call_count, 1)
        espera.assert_not_called()

    def test_limite_e_erros_permanentes(self):
        for status in (400, 401, 403, 404, 501):
            operacao = Mock(side_effect=erro_http(status))
            with self.subTest(status=status), patch.object(rede.time, "sleep") as espera, \
                 self.assertRaises(requests.HTTPError):
                rede.repetir_transitorios(operacao)()
            self.assertEqual(operacao.call_count, 1)
            espera.assert_not_called()
        operacao = Mock(side_effect=requests.Timeout("tempo esgotado"))
        with patch.object(rede.time, "sleep"), self.assertRaises(requests.Timeout):
            rede.repetir_transitorios(operacao)()
        self.assertEqual(operacao.call_count, 3)
        operacao = Mock(side_effect=requests.exceptions.SSLError("certificado"))
        with patch.object(rede.time, "sleep"), self.assertRaises(requests.exceptions.SSLError):
            rede.repetir_transitorios(operacao)()
        self.assertEqual(operacao.call_count, 1)

    def test_download_interrompido_recomeca_e_304_e_idempotente(self):
        with tempfile.TemporaryDirectory() as diretorio:
            pasta = Path(diretorio)
            (pasta / "raw").mkdir()
            (pasta / "processed").mkdir()
            interrompida = resposta(csv_sintetico())
            def interromper(**kwargs):
                yield b"parcial"
                raise requests.ConnectionError("interrompido")
            interrompida.iter_content = interromper
            sessao = Mock()
            sessao.get.side_effect = [interrompida, resposta(csv_sintetico()), resposta(status=304)]
            estado = {}
            with patch.object(rede.time, "sleep"):
                self.assertEqual(extracao_ons.atualizar_ano(sessao, 2026, url_ano(2026), pasta, estado), "atualizado")
                raw = pasta / "raw/CARGA_ENERGIA_2026.csv"
                conteudo = raw.read_bytes()
                self.assertEqual(extracao_ons.atualizar_ano(sessao, 2026, url_ano(2026), pasta, estado), "sem_alteracao")
            self.assertEqual(raw.read_bytes(), conteudo)
            self.assertEqual(sessao.get.call_count, 3)
            self.assertEqual(list((pasta / "raw").glob("*.part")), [])

    def test_extratores_aneel_e_geracao_recuperam_503(self):
        with tempfile.TemporaryDirectory() as diretorio:
            pasta = Path(diretorio)
            raw = pasta / "raw"
            raw.mkdir()
            recurso = {"ano": 2026, "mes": 9, "url": "https://exemplo.test/geracao"}
            with patch.object(rede.time, "sleep"), patch.object(extracao_geracao_ons, "RAW_DIR", raw), \
                 patch.object(extracao_geracao_ons, "STATE_FILE", pasta / "geracao.json"), \
                 patch.object(requests, "get", side_effect=[http(status=503), http(parquet())]) as get:
                self.assertEqual(extracao_geracao_ons.baixar_recurso(recurso, {}), "atualizado")
                self.assertEqual(get.call_count, 2)
                self.assertTrue(all(c.kwargs["timeout"] for c in get.call_args_list))
                self.assertTrue(all(c.kwargs.get("verify", True) for c in get.call_args_list))
            recurso_aneel = {"url": "https://exemplo.test/aneel", "hash": "v1"}
            with patch.object(rede.time, "sleep"), patch.object(extracao_aneel, "RAW_DIR", raw), \
                 patch.object(extracao_aneel, "STATE_FILE", pasta / "aneel.json"), \
                 patch.object(extracao_aneel, "ARQUIVO", raw / "siga.csv"), \
                 patch.object(extracao_aneel, "consultar_recurso", return_value=recurso_aneel), \
                 patch.object(requests, "get", side_effect=[http(status=503), http(csv_aneel())]) as get:
                extracao_aneel.atualizar()
                self.assertEqual(get.call_count, 2)
                self.assertTrue(all(c.kwargs["timeout"] for c in get.call_args_list))
                self.assertTrue(all(c.kwargs.get("verify", True) for c in get.call_args_list))


if __name__ == "__main__":
    unittest.main()
