"""Regressões de publicação e qualidade com dados sintéticos locais."""

from contextlib import redirect_stdout
import calendar
import io
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
import weakref
from unittest.mock import patch

import pandas as pd

from src import transformacao_ons as carga, transformacao_aneel as aneel
from src import transformacao_geracao_ons as geracao, publicacao


def carga_exemplo(datas, subsistemas=("N", "NE", "S", "SE")):
    return pd.DataFrame([
        {"din_instante": pd.Timestamp(dia), "id_subsistema": sub,
         "nom_subsistema": sub, "val_cargaenergiamwmed": 100.0}
        for dia in datas for sub in subsistemas
    ])


class CargaConfiabilidadeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.processed = self.raiz / "data/processed"
        self.silver = self.raiz / "data/silver"
        self.gold = self.raiz / "data/gold"
        for pasta in (self.processed, self.silver, self.gold):
            pasta.mkdir(parents=True)

    def executar(self):
        with patch.object(carga, "BASE_DIR", self.raiz), \
             patch.object(carga, "PROCESSED_DIR", self.processed), \
             patch.object(carga, "SILVER_DIR", self.silver), \
             patch.object(carga, "GOLD_DIR", self.gold), \
             redirect_stdout(io.StringIO()):
            carga.main()

    def test_falha_na_gold_nao_publica_silver_de_execucao_nova(self):
        carga_exemplo(["2026-01-01"]).to_parquet(self.processed / "carga_energia_2026.parquet")
        antigo_silver = self.silver / "carga_energia.parquet"
        antigo_gold = self.gold / "carga_mensal.parquet"
        antigo_silver.write_bytes(b"silver anterior")
        antigo_gold.write_bytes(b"gold anterior")
        with patch.object(carga, "criar_gold", side_effect=RuntimeError("falha simulada")):
            with self.assertRaisesRegex(RuntimeError, "falha simulada"):
                self.executar()
        self.assertEqual(antigo_silver.read_bytes(), b"silver anterior")
        self.assertEqual(antigo_gold.read_bytes(), b"gold anterior")

    def test_subsistema_ausente_impede_publicacao(self):
        carga_exemplo(["2026-01-01"], ("N", "NE", "S")).to_parquet(
            self.processed / "carga_energia_2026.parquet")
        with self.assertRaisesRegex(ValueError, "subsistema"):
            self.executar()
        self.assertFalse((self.silver / "carga_energia.parquet").exists())

    def test_calendario_bissexto_e_conversoes(self):
        dados = carga_exemplo(pd.date_range("2024-02-01", "2024-02-29"))
        dados.to_parquet(self.processed / "carga_energia_2024.parquet")
        self.executar()
        silver = pd.read_parquet(self.silver / "carga_energia.parquet")
        gold = pd.read_parquet(self.gold / "carga_mensal.parquet")
        self.assertTrue(gold["mes_completo"].all())
        self.assertTrue(gold["dias_no_mes"].eq(29).all())
        self.assertEqual(silver["energia_mwh"].sum(), 4 * 29 * 100 * 24)
        self.assertEqual(gold["energia_gwh"].sum(), 4 * 29 * 100 * 24 / 1000)
        self.assertAlmostEqual(30_000_000 / (8784 if calendar.isleap(2036) else 8760) / 1000,
                               3.4153005464480876)

    def test_dia_interno_ausente_bloqueia(self):
        carga_exemplo(["2026-01-01", "2026-01-03"]).to_parquet(
            self.processed / "carga_energia_2026.parquet")
        with self.assertRaisesRegex(ValueError, "Calendário"):
            self.executar()

    def test_chave_duplicada_bloqueia(self):
        dados = carga_exemplo(["2026-01-01"])
        pd.concat([dados, dados.iloc[:1]]).to_parquet(self.processed / "carga_energia_2026.parquet")
        with self.assertRaisesRegex(ValueError, "duplicados"):
            self.executar()

    def test_execucao_idempotente_e_mes_parcial(self):
        carga_exemplo(["2026-01-01", "2026-01-02"]).to_parquet(
            self.processed / "carga_energia_2026.parquet")
        self.executar()
        ponteiro = self.raiz / "data/.publicacoes/carga_ons/atual.json"
        antes = ponteiro.read_bytes()
        self.executar()
        self.assertEqual(antes, ponteiro.read_bytes())
        self.assertFalse(pd.read_parquet(self.gold / "carga_mensal.parquet")["mes_completo"].any())
        manifesto, arquivos = publicacao.ler_concluida(self.raiz, "carga_ons")
        self.assertEqual(len(arquivos), 2)
        self.assertEqual(manifesto["qualidade"]["meses_incompletos_por_subsistema"], 4)


def dados_geracao(mes, nomes, valores=None, horas=(0, 1)):
    linhas = []
    for nome in nomes:
        for i, hora in enumerate(horas):
            linhas.append({"din_instante": pd.Timestamp(f"2026-{mes:02d}-01 {hora:02d}:00"),
                           "id_subsistema": "N", "nom_subsistema": "Norte", "id_estado": "PA",
                           "nom_estado": "Para", "cod_modalidadeoperacao": "TIPO I",
                           "nom_tipousina": "HIDROELETRICA", "nom_tipocombustivel": "agua",
                           "nom_usina": nome, "id_ons": nome, "ceg": nome,
                           "val_geracao": valores[i] if valores is not None else 1.0})
    return pd.DataFrame(linhas)


class GeracaoQualidadeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pasta = Path(self.tmp.name)

    def silver(self, mes, dados):
        origem = self.pasta / f"geracao_usina_2026_{mes:02d}.parquet"
        dados.to_parquet(origem, index=False)
        return geracao.criar_silver(origem, self.pasta / f"silver_{mes}.parquet")

    def test_nulos_negativos_e_agregacao_preservados(self):
        df = self.silver(9, dados_geracao(9, ["A"], [None, -2.0]))
        gold = geracao.criar_gold_horaria(df)
        qualidade = geracao.criar_gold_qualidade(df)
        geracao.validar_resultados([df], gold, qualidade)
        self.assertEqual(int(df.geracao_ausente.sum()), 1)
        self.assertEqual(int(df.geracao_negativa.sum()), 1)
        self.assertTrue(pd.isna(gold.iloc[0].geracao_soma))
        self.assertEqual(gold.iloc[1].geracao_soma, -2.0)

    def test_infinitos_rejeitados_e_zero_preservado(self):
        df = self.silver(9, dados_geracao(9, ["A"], [None, 0.0]))
        self.assertTrue(pd.isna(df.val_geracao.iloc[0]))
        self.assertEqual(df.val_geracao.iloc[1], 0.0)
        self.assertTrue(df.geracao_zero.iloc[1])
        anterior = (self.pasta / "silver_9.parquet").read_bytes()
        for valor in (float("inf"), float("-inf")):
            with self.subTest(valor=valor):
                with self.assertRaisesRegex(ValueError, "infinitos"):
                    self.silver(9, dados_geracao(9, ["A"], [1.0, valor]))
                self.assertEqual((self.pasta / "silver_9.parquet").read_bytes(), anterior)

    def test_infinito_em_mes_posterior_nao_promove_nenhuma_tabela(self):
        raiz = self.pasta / "projeto"
        raw = raiz / "data/raw/ons/geracao/2026"
        silver = raiz / "data/silver/ons/geracao"
        gold = raiz / "data/gold/ons/geracao"
        for pasta in (raw, silver, gold):
            pasta.mkdir(parents=True)
        anterior = silver / "geracao_2026_09.parquet"
        anterior.write_bytes(b"versao anterior")
        dados_geracao(9, ["A"]).to_parquet(raw / "geracao_usina_2026_09.parquet")
        dados_geracao(10, ["A"], [1.0, float("inf")]).to_parquet(
            raw / "geracao_usina_2026_10.parquet")
        with patch.object(geracao, "BASE_DIR", raiz), patch.object(geracao, "RAW_DIR", raw), \
             patch.object(geracao, "SILVER_DIR", silver), patch.object(geracao, "GOLD_DIR", gold), \
             redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "infinitos"):
            geracao.main("2026-09", "2026-10")
        self.assertEqual(anterior.read_bytes(), b"versao anterior")
        self.assertFalse((silver / "geracao_2026_10.parquet").exists())
        self.assertFalse(any(gold.iterdir()))

    def test_duplicacao_e_competencia_divergente_bloqueiam(self):
        dados = dados_geracao(9, ["A"])
        with self.assertRaisesRegex(ValueError, "repetida"):
            self.silver(9, pd.concat([dados, dados.iloc[:1]]))
        with self.assertRaisesRegex(ValueError, "Competência"):
            self.silver(10, dados)

    def test_mudanca_universo_e_periodo_parcial(self):
        a = self.silver(9, dados_geracao(9, ["A", "B"]))
        b = self.silver(10, dados_geracao(10, ["B", "C"]))
        q = geracao.diagnosticar_periodo([a, b], "2026-09", "2026-10")
        self.assertEqual((q["mudancas_universo"][0]["entradas"], q["mudancas_universo"][0]["saidas"]), (1, 1))
        self.assertEqual(len(q["alertas"]), 3)
        self.assertEqual(q["competencias"]["2026-09"]["horas_recebidas"], 2)
        with self.assertRaisesRegex(ValueError, "ausentes ou extras"):
            geracao.diagnosticar_periodo([a, b], "2026-09", "2026-11")

    def test_hora_interna_ausente_bloqueia(self):
        df = self.silver(9, dados_geracao(9, ["A"], horas=(0, 2)))
        with self.assertRaisesRegex(ValueError, "Horas internas"):
            geracao.diagnosticar_periodo([df])

    def test_ausencia_de_entidade_em_hora_e_alerta(self):
        dados = dados_geracao(9, ["A", "B"])
        dados = dados[~((dados.nom_usina == "B") & (dados.din_instante.dt.hour == 1))]
        diagnostico = geracao.diagnosticar_periodo([self.silver(9, dados)])
        parciais = diagnostico["competencias"]["2026-09"]["entidades_com_horas_ausentes"]
        self.assertEqual(len(parciais), 1)
        self.assertEqual(parciais[0]["horas_presentes"], 1)
        self.assertTrue(any("Entidades com horas ausentes" in a for a in diagnostico["alertas"]))

    def test_pipeline_libera_silver_mensal_e_publica_versao(self):
        raiz = self.pasta / "projeto"
        raw = raiz / "data/raw/ons/geracao/2026"
        raw.mkdir(parents=True)
        for mes in (9, 10):
            dados_geracao(mes, ["A"], [None, -2.0]).to_parquet(
                raw / f"geracao_usina_2026_{mes:02d}.parquet", index=False)
        silver_dir = raiz / "data/silver/ons/geracao"
        gold_dir = raiz / "data/gold/ons/geracao"
        silver_dir.mkdir(parents=True)
        gold_dir.mkdir(parents=True)
        original = geracao.criar_silver
        referencias = []
        def acompanhar(*args, **kwargs):
            if referencias:
                self.assertIsNone(referencias[-1](), "Silver anterior continua retida")
            df = original(*args, **kwargs)
            referencias.append(weakref.ref(df))
            return df
        with patch.object(geracao, "BASE_DIR", raiz), patch.object(geracao, "RAW_DIR", raw), \
             patch.object(geracao, "SILVER_DIR", silver_dir), patch.object(geracao, "GOLD_DIR", gold_dir), \
             patch.object(geracao, "criar_silver", side_effect=acompanhar), redirect_stdout(io.StringIO()):
            geracao.main("2026-09", "2026-10")
        manifesto, arquivos = publicacao.ler_concluida(raiz, "geracao_ons")
        self.assertEqual(len(arquivos), 4)
        self.assertEqual(manifesto["qualidade"]["competencias"]["2026-09"]["nulos"], 1)
        gold = pd.read_parquet(arquivos["data/gold/ons/geracao/geracao_horaria.parquet"])
        self.assertEqual(gold["valores_negativos"].sum(), 2)


class AneelQualidadeTest(unittest.TestCase):
    def test_deduplicacao_equivalente_e_conflitante(self):
        linha = {col: "x" for col in aneel.ATRIBUTOS_ESSENCIAIS}
        linha["cod_ceg"] = "A"
        base = pd.DataFrame([linha, linha.copy()])
        self.assertEqual(len(aneel.tratar_duplicidades(base)), 1)
        base.loc[1, "fase"] = "outra"
        with self.assertRaisesRegex(ValueError, "conflitantes"):
            aneel.tratar_duplicidades(base)

    def test_capacidade_por_fase_e_conversao_kw_mw_gw(self):
        with tempfile.TemporaryDirectory() as pasta:
            dados = pd.DataFrame([
                {"cod_ceg": "A", "em_operacao": True, "tipo_geracao": "UHE", "origem": "agua",
                 "fonte": "hidrica", "uf": "PA", "fase": "Operacao", "potencia_fiscalizada_mw": 2.0,
                 "potencia_outorgada_mw": 3.0},
                {"cod_ceg": "B", "em_operacao": False, "tipo_geracao": "UHE", "origem": "agua",
                 "fonte": "hidrica", "uf": "PA", "fase": "Construcao", "potencia_fiscalizada_mw": 0.0,
                 "potencia_outorgada_mw": 4.0}])
            fontes = aneel.criar_gold_fontes(dados, Path(pasta) / "fontes.parquet")
            estados = aneel.criar_gold_estados(dados, Path(pasta) / "estados.parquet")
            self.assertEqual(fontes.potencia_mw.sum(), 2000 / 1000)
            self.assertEqual(fontes.potencia_mw.sum() / 1000, 0.002)
            self.assertEqual(estados.set_index("fase").loc["Construcao", "potencia_mw"], 4.0)


class PublicacaoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.caminhos = ["data/silver/a.csv", "data/gold/b.csv"]

    def publicar(self, valores):
        with publicacao.Publicacao(self.raiz, "teste") as pub:
            for caminho, valor in zip(self.caminhos, valores):
                pub.caminho(caminho).write_text(valor, encoding="utf-8")
            return pub.confirmar(self.caminhos)

    def test_publicacao_multiplas_tabelas_e_idempotencia(self):
        primeiro = self.publicar(["a", "b"])
        self.assertEqual(self.publicar(["a", "b"]), primeiro)
        manifesto, caminhos = publicacao.ler_concluida(self.raiz, "teste")
        self.assertEqual(manifesto["id"], primeiro)
        self.assertEqual([caminhos[c].read_text() for c in self.caminhos], ["a", "b"])

    def test_idempotencia_com_entidades_representadas_por_tuplas(self):
        ids = []
        for _ in range(2):
            with publicacao.Publicacao(self.raiz, "teste") as pub:
                pub.caminho(self.caminhos[0]).write_text("a")
                ids.append(pub.confirmar([self.caminhos[0]], {"entradas": [("N", "Usina")]}))
        self.assertEqual(ids[0], ids[1])

    def test_falha_antes_da_promocao_preserva_anterior(self):
        anterior = self.publicar(["a", "b"])
        with self.assertRaises(FileNotFoundError):
            with publicacao.Publicacao(self.raiz, "teste") as pub:
                pub.caminho(self.caminhos[0]).write_text("novo")
                pub.confirmar(self.caminhos)
        self.assertEqual(publicacao.ler_concluida(self.raiz, "teste")[0]["id"], anterior)
        self.assertEqual((self.raiz / self.caminhos[0]).read_text(), "a")

    def test_falha_durante_promocao_reverte_todas_as_tabelas(self):
        anterior = self.publicar(["a", "b"])
        copiar = publicacao._copiar_atomico
        def falhar(origem, destino):
            if Path(destino).resolve() == (self.raiz / self.caminhos[1]).resolve() and "execucoes" in str(origem):
                raise OSError("falha na segunda promocao")
            return copiar(origem, destino)
        with patch.object(publicacao, "_copiar_atomico", side_effect=falhar), self.assertRaises(OSError):
            self.publicar(["novo a", "novo b"])
        self.assertEqual([(self.raiz / c).read_text() for c in self.caminhos], ["a", "b"])
        self.assertEqual(publicacao.ler_concluida(self.raiz, "teste")[0]["id"], anterior)

    def test_recuperacao_apos_interrupcao_simulada(self):
        anterior = self.publicar(["a", "b"])
        copiar = publicacao._copiar_atomico
        def interromper(origem, destino):
            if Path(destino).resolve() == (self.raiz / self.caminhos[1]).resolve() and "execucoes" in str(origem):
                raise OSError("interrupcao")
            return copiar(origem, destino)
        recuperar = publicacao.Publicacao.recuperar
        chamadas = 0
        def sem_recuperar_no_fim(instancia):
            nonlocal chamadas
            chamadas += 1
            if chamadas == 2:
                raise OSError("processo interrompido antes do rollback")
            return recuperar(instancia)
        with patch.object(publicacao, "_copiar_atomico", side_effect=interromper), \
             patch.object(publicacao.Publicacao, "recuperar", sem_recuperar_no_fim), self.assertRaises(OSError):
            self.publicar(["novo a", "novo b"])
        self.assertEqual((self.raiz / self.caminhos[0]).read_text(), "novo a")
        self.assertEqual(publicacao.ler_concluida(self.raiz, "teste")[0]["id"], anterior)
        with publicacao.Publicacao(self.raiz, "teste"):
            pass
        self.assertEqual([(self.raiz / c).read_text() for c in self.caminhos], ["a", "b"])

    def test_bytes_adulterados_na_versao_sao_rejeitados(self):
        self.publicar(["a", "b"])
        _, arquivos = publicacao.ler_concluida(self.raiz, "teste")
        arquivos[self.caminhos[0]].write_text("x")
        with self.assertRaisesRegex(ValueError, "Integridade"):
            publicacao.ler_concluida(self.raiz, "teste")

    def test_leitor_observa_versao_anterior_durante_promocao(self):
        anterior = self.publicar(["a", "b"])
        copiar = publicacao._copiar_atomico
        observadas = []
        def observar(origem, destino):
            resultado = copiar(origem, destino)
            if Path(destino).resolve() == (self.raiz / self.caminhos[0]).resolve() and "execucoes" in str(origem):
                manifesto, arquivos = publicacao.ler_concluida(self.raiz, "teste")
                observadas.append((manifesto["id"], [arquivos[c].read_text() for c in self.caminhos]))
            return resultado
        with patch.object(publicacao, "_copiar_atomico", side_effect=observar):
            nova = self.publicar(["novo a", "novo b"])
        self.assertEqual(observadas, [(anterior, ["a", "b"])])
        self.assertNotEqual(nova, anterior)
        self.assertEqual(publicacao.ler_concluida(self.raiz, "teste")[0]["id"], nova)

    def test_bloqueio_de_publicacoes_concorrentes(self):
        with publicacao.Publicacao(self.raiz, "teste"):
            with self.assertRaisesRegex(RuntimeError, "andamento"):
                with publicacao.Publicacao(self.raiz, "teste"):
                    pass

    def test_interrupcao_real_de_processo_recupera(self):
        anterior = self.publicar(["a", "b"])
        codigo = '''
import os, sys
from pathlib import Path
from src import publicacao
raiz = Path(sys.argv[1]).resolve()
caminhos = ["data/silver/a.csv", "data/gold/b.csv"]
copiar = publicacao._copiar_atomico
def interromper(origem, destino):
    copiar(origem, destino)
    if Path(destino).resolve() == raiz / caminhos[0] and "execucoes" in str(origem):
        os._exit(73)
publicacao._copiar_atomico = interromper
with publicacao.Publicacao(raiz, "teste") as pub:
    for caminho in caminhos:
        pub.caminho(caminho).write_text("novo")
    pub.confirmar(caminhos)
'''
        processo = subprocess.run([sys.executable, "-B", "-c", codigo, str(self.raiz)],
                                   cwd=Path(__file__).resolve().parents[1], capture_output=True)
        self.assertEqual(processo.returncode, 73, processo.stderr)
        self.assertEqual(publicacao.ler_concluida(self.raiz, "teste")[0]["id"], anterior)
        with publicacao.Publicacao(self.raiz, "teste"):
            pass
        self.assertEqual([(self.raiz / c).read_text() for c in self.caminhos], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
