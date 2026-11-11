"""Versionamento e seleção de períodos: fixtures sintéticas, sem rede."""

from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import pandas as pd
import requests

from src import edicoes, snapshots, extracao_aneel, extracao_geracao_ons, extracao_ons


def resposta(corpo):
    http = requests.Response()
    http.status_code = 200
    http._content = corpo
    http._content_consumed = True
    http.headers = {"ETag": '"controle"'}
    return http


class EdicoesTest(unittest.TestCase):
    def setUp(self):
        self.temporario = tempfile.TemporaryDirectory(prefix="edicao_teste_")
        self.addCleanup(self.temporario.cleanup)
        self.raiz = Path(self.temporario.name)
        self.origem = self.raiz / "origem"
        self.projeto = self.raiz / "projeto"
        self.store = self.raiz / "snapshots"
        self.manifesto_path = self.raiz / "edicoes/2026-11-09.json"
        self.projeto.mkdir()
        (self.projeto / "requirements.txt").write_text("pandas\n", encoding="utf-8")
        (self.projeto / "src").mkdir()
        (self.projeto / "src/calculo.py").write_text("# implementacao original\n", encoding="utf-8")
        carga = self.origem / "data/raw/CARGA_ENERGIA_2026.csv"
        carga.parent.mkdir(parents=True)
        carga.write_text("din_instante;val_cargaenergiamwmed\n2026-01-01;100\n", encoding="utf-8")
        self.geracao = self.origem / "data/raw/ons/geracao/2026/geracao_usina_2026_09.parquet"
        self.geracao.parent.mkdir(parents=True)
        pd.DataFrame({"din_instante": pd.to_datetime(["2026-09-01"]), "val_geracao": [10.0]}).to_parquet(self.geracao)
        self.aneel = self.origem / edicoes.ANEEL_RAW
        self.aneel.parent.mkdir(parents=True)
        self.aneel.write_text("DatGeracaoConjuntoDados;NomEmpreendimento\n2026-11-09;Teste\n", encoding="utf-8")
        epe = self.origem / "data/reference/epe_pde2036.csv"
        epe.parent.mkdir(parents=True)
        epe.write_text("versao,url_fonte,valor_twh\nconsulta_publica_2026,https://www.epe.gov.br/,30\n", encoding="utf-8")
        (self.origem / "data/controle_aneel.json").write_text(json.dumps({
            "baixado_em": "2026-11-09T16:26:51+00:00", "hash": "hash_do_catalogo",
            "url": "https://dadosabertos.aneel.gov.br/dataset/recurso.csv"}), encoding="utf-8")
        self.manifesto = edicoes.preservar("2026-11-09", self.origem, self.store, self.manifesto_path,
                                         projeto=self.projeto)

    def test_manifesto_completo_aquisicao_desconhecida_e_hash_dos_bytes(self):
        self.assertEqual(self.manifesto["versao_formato"], 1)
        self.assertEqual(self.manifesto["configuracao"]["aneel_referencia"], "2026-11-09")
        self.assertEqual(self.manifesto["distribuicao"]["status"], "pendente_publicacao")
        snapshots.verificar(self.manifesto, self.store)
        for item in self.manifesto["arquivos"]:
            self.assertEqual(len(item["sha256"]), 64)
            self.assertTrue(snapshots.objeto(self.store, item).is_file())
        carga = next(a for a in self.manifesto["arquivos"] if a["dataset"] == "carga-energia")
        self.assertIsNone(carga["data_aquisicao"])
        self.assertEqual(carga["situacao_aquisicao"], "nao_registrada")
        aneel = next(a for a in self.manifesto["arquivos"] if a["fonte"] == "ANEEL")
        self.assertEqual(aneel["data_aquisicao"], "2026-11-09T16:26:51+00:00")
        self.assertNotEqual(aneel["sha256"], aneel["metadados_origem_registrados"]["hash"])

    def test_preservacao_idempotente_e_protecao_da_edicao(self):
        antes = self.manifesto_path.read_bytes()
        hashes = {p.name: p.stat().st_mtime_ns for p in (self.store / "sha256").iterdir()}
        edicoes.preservar("2026-11-09", self.origem, self.store, self.manifesto_path, projeto=self.projeto)
        self.assertEqual(antes, self.manifesto_path.read_bytes())
        self.assertEqual(hashes, {p.name: p.stat().st_mtime_ns for p in (self.store / "sha256").iterdir()})
        self.aneel.write_text(self.aneel.read_text().replace("Teste", "Outro"), encoding="utf-8")
        with self.assertRaises(FileExistsError):
            edicoes.preservar("2026-11-09", self.origem, self.store, self.manifesto_path, projeto=self.projeto)
        self.assertEqual(antes, self.manifesto_path.read_bytes())
        snapshots.verificar(self.manifesto, self.store)

    def test_revisao_fonte_vira_outra_edicao_sem_modificar_a_anterior(self):
        original = self.aneel.read_bytes()
        self.aneel.write_bytes(original.replace(b"Teste", b"Outro"))
        diferencas = edicoes.comparar(self.manifesto, self.origem / "data")
        self.assertEqual(sum(d["situacao"] == "conteudo_divergente" for d in diferencas), 1)
        nova = edicoes.preservar("revisao-1", self.origem, self.store,
                                self.raiz / "edicoes/revisao-1.json", projeto=self.projeto)
        self.assertNotEqual(nova["edicao"], self.manifesto["edicao"])
        item = next(a for a in self.manifesto["arquivos"] if a["fonte"] == "ANEEL")
        self.assertEqual(snapshots.objeto(self.store, item).read_bytes(), original)
        self.assertEqual(item["metadados_origem_registrados"]["hash"], "hash_do_catalogo")

    def test_hash_divergente_recusa_reutilizacao_e_nao_autocorrige_objeto(self):
        item = self.manifesto["arquivos"][0]
        objeto = snapshots.objeto(self.store, item)
        objeto.write_bytes(b"x" * item["tamanho_bytes"])
        with self.assertRaisesRegex(ValueError, "Integridade divergente"):
            snapshots.verificar(self.manifesto, self.store)
        with self.assertRaises(ValueError):
            edicoes.reproduzir(self.manifesto_path, self.store, self.raiz / "restaurada", executar=False)
        self.assertFalse((self.raiz / "restaurada").exists())

    def test_snapshot_ausente_falha_sem_fallback_remoto(self):
        item = self.manifesto["arquivos"][0]
        snapshots.objeto(self.store, item).unlink()
        with patch.object(requests, "get") as rede, self.assertRaises(FileNotFoundError):
            edicoes.reproduzir(self.manifesto_path, self.store, self.raiz / "ausente", executar=False)
        rede.assert_not_called()

    def test_hash_registrado_na_origem_divergente_impede_preservacao(self):
        controle = {"2026-09": {"sha256": "0" * 64, "verificado_em": "2026-11-09T12:00:00+00:00"}}
        (self.origem / "data/controle_geracao_ons.json").write_text(json.dumps(controle), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "diverge do controle"):
            edicoes.preservar("divergente", self.origem, self.store, self.raiz / "divergente.json",
                              projeto=self.projeto)
        self.assertFalse((self.raiz / "divergente.json").exists())

    def test_reproducao_reutiliza_codigo_preservado_e_recusa_extras(self):
        (self.projeto / "src/calculo.py").write_text("# codigo futuro", encoding="utf-8")
        destino = self.raiz / "reproducao"
        edicoes.reproduzir(self.manifesto_path, self.store, destino, executar=False)
        self.assertEqual((destino / "src/calculo.py").read_text(), "# implementacao original\n")
        edicoes.reproduzir(self.manifesto_path, self.store, destino, executar=False)
        (destino / "data/raw/extra.csv").write_text("dados extras", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "insumos extras"):
            edicoes.reproduzir(self.manifesto_path, self.store, destino, executar=False)

    def test_destino_ocupado_nao_e_sobrescrito(self):
        destino = self.raiz / "ocupado"
        destino.mkdir()
        (destino / "meu_arquivo.txt").write_text("preservar")
        with self.assertRaises(FileExistsError):
            edicoes.reproduzir(self.manifesto_path, self.store, destino, executar=False)
        self.assertEqual((destino / "meu_arquivo.txt").read_text(), "preservar")

    def test_configuracao_historica_nao_rotula_fotografia_de_outra_data(self):
        with self.assertRaisesRegex(ValueError, "aneel_referencia"):
            edicoes.preservar("errada", self.origem, self.store, self.raiz / "errada.json",
                              {"aneel_referencia": "2026-11-08"}, projeto=self.projeto)
        self.assertFalse((self.raiz / "errada.json").exists())

    def test_manifesto_incompleto_caminho_inseguro_e_duplicado(self):
        for campo in ("sha256", "data_aquisicao", "recurso", "referencia", "tamanho_bytes"):
            invalido = copy.deepcopy(self.manifesto)
            del invalido["arquivos"][0][campo]
            with self.subTest(campo=campo), self.assertRaises(ValueError):
                snapshots.validar_manifesto(invalido)
        for caminho in ("../fora", "C:/fora", "/fora", "data/../../fora", "data\\fora"):
            invalido = copy.deepcopy(self.manifesto)
            invalido["arquivos"][0]["caminho"] = caminho
            with self.subTest(caminho=caminho), self.assertRaises(ValueError):
                snapshots.validar_manifesto(invalido)
        invalido = copy.deepcopy(self.manifesto)
        invalido["arquivos"].append(invalido["arquivos"][0])
        with self.assertRaises(ValueError):
            snapshots.validar_manifesto(invalido)

    def test_pacote_exportacao_importacao_verificada_e_idempotente(self):
        pacote = self.raiz / "edicao.zip"
        snapshots.exportar(self.manifesto_path, self.store, pacote)
        recuperado = self.raiz / "recuperado"
        for _ in range(2):
            snapshots.importar(self.manifesto_path, recuperado, pacote)
            snapshots.verificar(self.manifesto, recuperado)
        with self.assertRaises(FileExistsError):
            snapshots.exportar(self.manifesto_path, self.store, pacote)
        adulterado = self.raiz / "adulterado.zip"
        with zipfile.ZipFile(pacote) as entrada, zipfile.ZipFile(adulterado, "w") as saida:
            for item in entrada.infolist():
                corpo = entrada.read(item.filename)
                if item.filename.startswith("sha256/"):
                    corpo = b"x" * len(corpo)
                saida.writestr(item.filename, corpo)
        with self.assertRaises(ValueError):
            snapshots.importar(self.manifesto_path, self.raiz / "nao_importado", adulterado)
        self.assertFalse((self.raiz / "nao_importado").exists())

    def test_aneel_snapshot_local_sem_rede_e_sem_sobrescrita(self):
        destino = self.raiz / "aneel_reuso/data"
        argv = ["extracao_aneel.py", "--snapshot", "2026-11-09", "--manifestos", str(self.manifesto_path.parent),
                "--armazenamento", str(self.store), "--data-dir", str(destino)]
        with patch.object(extracao_aneel, "RAW_DIR"), patch.object(extracao_aneel, "STATE_FILE"), \
                patch.object(extracao_aneel, "ARQUIVO"), patch.object(sys, "argv", argv), \
                patch.object(requests, "get") as rede, redirect_stdout(io.StringIO()):
            extracao_aneel.main()
            extracao_aneel.main()
            arquivo = destino / "raw/aneel/siga_empreendimentos_diario.csv"
            self.assertEqual(arquivo.read_bytes(), self.aneel.read_bytes())
            arquivo.write_bytes(b"outro snapshot")
            with self.assertRaises(ValueError):
                extracao_aneel.main()
            rede.assert_not_called()

    def test_extratores_recusam_escrita_em_area_historica(self):
        destino = self.raiz / "historica"
        edicoes.reproduzir(self.manifesto_path, self.store, destino, executar=False)
        for modulo, argumentos in ((extracao_ons, ["--anos", "2026"]),
                                    (extracao_aneel, ["--atualizar"]),
                                    (extracao_geracao_ons, ["--recentes", "3"])):
            for pasta in (destino / "data", self.store / "sha256"):
                with self.subTest(modulo=modulo.__name__, pasta=str(pasta)), \
                        patch.object(sys, "argv", ["extrator", *argumentos, "--data-dir", str(pasta)]), \
                        patch.object(requests, "get") as rede, self.assertRaisesRegex(ValueError, "protegida"):
                    modulo.main()
                rede.assert_not_called()


class PeriodosTest(unittest.TestCase):
    def recursos(self):
        return [{"ano": 2026, "mes": mes, "url": f"https://dados.ons.org.br/{mes}.parquet",
                 "nome": f"Geracao_Usina_2-2026-{mes:02d}"} for mes in (8, 9, 10, 11)]

    def test_cli_baixa_apenas_recursos_no_intervalo(self):
        with tempfile.TemporaryDirectory() as pasta:
            argv = ["extracao_geracao_ons.py", "--inicio", "2026-09", "--fim", "2026-10", "--data-dir", pasta]
            def parquet_mensal(url, **kwargs):
                mes = int(url.rsplit("/", 1)[-1].split(".")[0])
                dados = {coluna: ["teste"] for coluna in (
                    "id_subsistema", "nom_subsistema", "id_estado", "nom_estado",
                    "cod_modalidadeoperacao", "nom_tipousina", "nom_tipocombustivel",
                    "nom_usina", "id_ons", "ceg")}
                dados.update(din_instante=pd.to_datetime([f"2026-{mes:02d}-01"]), val_geracao=[1.0])
                buffer = io.BytesIO()
                pd.DataFrame(dados).to_parquet(buffer)
                return resposta(buffer.getvalue())
            with patch.object(sys, "argv", argv), patch.object(extracao_geracao_ons, "RAW_DIR"), \
                    patch.object(extracao_geracao_ons, "STATE_FILE"), \
                    patch.object(extracao_geracao_ons, "consultar_recursos", return_value=self.recursos()), \
                    patch.object(requests, "get", side_effect=parquet_mensal) as rede, \
                    redirect_stdout(io.StringIO()):
                extracao_geracao_ons.main()
                self.assertEqual([c.args[0] for c in rede.call_args_list],
                                 ["https://dados.ons.org.br/9.parquet", "https://dados.ons.org.br/10.parquet"])
                self.assertEqual(len(list(Path(pasta).rglob("*.parquet"))), 2)

    def test_recentes_e_falta_de_competencia(self):
        selecionar = extracao_geracao_ons.selecionar_recursos
        self.assertEqual([r["mes"] for r in selecionar(self.recursos(), recentes=2)], [10, 11])
        with self.assertRaisesRegex(ValueError, "indisponíveis"):
            selecionar(self.recursos(), "2026-07", "2026-10")
        with self.assertRaisesRegex(ValueError, "ambíguo"):
            selecionar(self.recursos() + [self.recursos()[1]], "2026-09", "2026-10")
        with self.assertRaisesRegex(ValueError, "ambíguo"):
            selecionar(self.recursos() + [self.recursos()[1]], recentes=3)

    def test_cli_invalida_falha_antes_de_consultar_catalogo(self):
        for argumentos in ([], ["--inicio", "2026-09"], ["--recentes", "0"],
                            ["--inicio", "2026-13", "--fim", "2026-14"],
                            ["--inicio", "2026-10", "--fim", "2026-09"],
                            ["--recentes", "3", "--inicio", "2026-09", "--fim", "2026-10"]):
            with self.subTest(argumentos=argumentos), patch.object(sys, "argv", ["extrator", *argumentos]), \
                    patch.object(extracao_geracao_ons, "consultar_recursos") as catalogo, \
                    patch.object(sys, "stderr", io.StringIO()), self.assertRaises(SystemExit):
                extracao_geracao_ons.main()
            catalogo.assert_not_called()


if __name__ == "__main__":
    unittest.main()
