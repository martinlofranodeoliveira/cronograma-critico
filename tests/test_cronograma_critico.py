"""Suíte completa de testes unitários e de integração para cronograma-critico."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import contextlib
import copy
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cronograma_critico import (  # noqa: E402
    ARQUIVOS_PACOTE,
    CronogramaError,
    __version__,
    build_bundle,
    dumps,
    loads,
    parse_project,
    plan_and_write,
    plan_json,
    plan_schedule,
    write_bundle,
)
from cronograma_critico.cli import main as cli_main  # noqa: E402


def _base_doc() -> dict:
    return {
        "schema_version": 1,
        "projeto": {
            "id": "PROJ-BASE",
            "titulo": "Projeto Base de Teste",
            "data_inicio": "2026-03-02",
        },
        "calendario": {
            "dias_uteis_semana": [1, 2, 3, 4, 5],
            "feriados": [],
        },
        "orcamento_caminhos": 100,
        "tarefas": [
            {"id": "T1", "nome": "Tarefa 1", "duracao_dias": 3, "setor": "Engenharia"},
            {"id": "T2", "nome": "Tarefa 2", "duracao_dias": 4, "setor": "Execução"},
        ],
        "dependencias": [
            {"id": "D1", "origem": "T1", "destino": "T2", "tipo": "FS", "defasagem_dias": 0},
        ],
    }


class TestJsonEstrito(unittest.TestCase):
    """Testa o parser e o serializador JSON estrito (jsoncodec.py)."""

    def test_01_loads_inteiro_valido(self) -> None:
        doc = loads('{"a": 0, "b": -42, "c": 123456, "d": true, "e": null}')
        self.assertEqual(doc["a"], 0)
        self.assertEqual(doc["b"], -42)
        self.assertIs(doc["d"], True)

    def test_02_recusa_float_decimal(self) -> None:
        with self.assertRaises(CronogramaError) as ctx:
            loads('{"duracao_dias": 2.5}')
        self.assertEqual(ctx.exception.code, "NUMERO_NAO_INTEIRO")

    def test_03_recusa_float_ponto_zero(self) -> None:
        with self.assertRaises(CronogramaError) as ctx:
            loads('{"duracao_dias": 3.0}')
        self.assertEqual(ctx.exception.code, "NUMERO_NAO_INTEIRO")

    def test_04_recusa_notacao_cientifica(self) -> None:
        with self.assertRaises(CronogramaError) as ctx:
            loads('{"duracao_dias": 1e2}')
        self.assertEqual(ctx.exception.code, "NUMERO_NAO_INTEIRO")

    def test_05_recusa_nan_e_infinity(self) -> None:
        for invalido in ('{"x": NaN}', '{"x": Infinity}', '{"x": -Infinity}'):
            with self.assertRaises(CronogramaError) as ctx:
                loads(invalido)
            self.assertEqual(ctx.exception.code, "JSON_INVALIDO")

    def test_06_recusa_chave_duplicada(self) -> None:
        with self.assertRaises(CronogramaError) as ctx:
            loads('{"id": "A", "id": "B"}')
        self.assertEqual(ctx.exception.code, "CHAVE_DUPLICADA")

    def test_07_recusa_bom_utf8_bytes_e_str(self) -> None:
        with self.assertRaises(CronogramaError) as ctx1:
            loads(b'\xef\xbb\xbf{"a": 1}')
        self.assertEqual(ctx1.exception.code, "JSON_INVALIDO")
        with self.assertRaises(CronogramaError) as ctx2:
            loads('\ufeff{"a": 1}')
        self.assertEqual(ctx2.exception.code, "JSON_INVALIDO")

    def test_08_recusa_utf8_invalido(self) -> None:
        with self.assertRaises(CronogramaError) as ctx:
            loads(b'{"a": "\xff\xfe"}')
        self.assertEqual(ctx.exception.code, "JSON_INVALIDO")

    def test_09_recusa_profundidade_excessiva(self) -> None:
        profundo = "[" * 20 + "1" + "]" * 20
        with self.assertRaises(CronogramaError) as ctx:
            loads(profundo)
        self.assertEqual(ctx.exception.code, "LIMITE_JSON")

    def test_10_recusa_inteiro_com_muitos_digitos(self) -> None:
        grande = '{"x": ' + ("9" * 25) + "}"
        with self.assertRaises(CronogramaError) as ctx:
            loads(grande)
        self.assertEqual(ctx.exception.code, "LIMITE_JSON")

    def test_11_dumps_recusa_float(self) -> None:
        with self.assertRaises(CronogramaError) as ctx:
            dumps({"x": 1.5})
        self.assertEqual(ctx.exception.code, "NUMERO_NAO_INTEIRO")

    def test_12_loads_recusa_tipo_nao_bytes_ou_str(self) -> None:
        with self.assertRaises(CronogramaError) as ctx:
            loads(12345)  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.code, "JSON_INVALIDO")


class TestEsquemaEValidacao(unittest.TestCase):
    """Testa validação estrita do esquema de entrada."""

    def test_13_recusa_schema_version_diferente_de_1(self) -> None:
        doc = _base_doc()
        doc["schema_version"] = 2
        with self.assertRaises(CronogramaError) as ctx:
            parse_project(doc)
        self.assertEqual(ctx.exception.code, "VERSAO_ESQUEMA_INVALIDA")

    def test_14_recusa_booleano_como_inteiro(self) -> None:
        doc = _base_doc()
        doc["tarefas"][0]["duracao_dias"] = True
        with self.assertRaises(CronogramaError) as ctx:
            parse_project(doc)
        self.assertEqual(ctx.exception.code, "ESQUEMA_INVALIDO")

    def test_15_recusa_campo_desconhecido_raiz_e_aninhado(self) -> None:
        doc1 = _base_doc()
        doc1["extra"] = 1
        with self.assertRaises(CronogramaError) as ctx1:
            parse_project(doc1)
        self.assertEqual(ctx1.exception.code, "CAMPO_DESCONHECIDO")

        doc2 = _base_doc()
        doc2["tarefas"][0]["prioridade"] = 1
        with self.assertRaises(CronogramaError) as ctx2:
            parse_project(doc2)
        self.assertEqual(ctx2.exception.code, "CAMPO_DESCONHECIDO")

    def test_16_recusa_campo_obrigatorio_ausente(self) -> None:
        doc = _base_doc()
        del doc["projeto"]["titulo"]
        with self.assertRaises(CronogramaError) as ctx:
            parse_project(doc)
        self.assertEqual(ctx.exception.code, "ESQUEMA_INVALIDO")

    def test_17_recusa_id_nao_ascii_ou_vazio(self) -> None:
        for id_invalido in ("", "TAREFA COM ESPACO", "TAREFA-Ç", "-INICIO-TRACO"):
            doc = _base_doc()
            doc["tarefas"][0]["id"] = id_invalido
            with self.assertRaises(CronogramaError) as ctx:
                parse_project(doc)
            self.assertEqual(ctx.exception.code, "ESQUEMA_INVALIDO")

    def test_18_recusa_tarefa_duplicada(self) -> None:
        doc = _base_doc()
        doc["tarefas"][1]["id"] = "T1"
        with self.assertRaises(CronogramaError) as ctx:
            parse_project(doc)
        self.assertEqual(ctx.exception.code, "TAREFA_DUPLICADA")

    def test_19_recusa_dependencia_id_ou_tripla_duplicada(self) -> None:
        doc = _base_doc()
        doc["dependencias"].append(
            {"id": "D1", "origem": "T1", "destino": "T2", "tipo": "SS", "defasagem_dias": 0}
        )
        with self.assertRaises(CronogramaError) as ctx1:
            parse_project(doc)
        self.assertEqual(ctx1.exception.code, "DEPENDENCIA_DUPLICADA")

        doc2 = _base_doc()
        doc2["dependencias"].append(
            {"id": "D2", "origem": "T1", "destino": "T2", "tipo": "FS", "defasagem_dias": 1}
        )
        with self.assertRaises(CronogramaError) as ctx2:
            parse_project(doc2)
        self.assertEqual(ctx2.exception.code, "DEPENDENCIA_DUPLICADA")

    def test_20_recusa_tarefa_inexistente_em_dependencia(self) -> None:
        doc = _base_doc()
        doc["dependencias"][0]["destino"] = "INEXISTENTE"
        with self.assertRaises(CronogramaError) as ctx:
            parse_project(doc)
        self.assertEqual(ctx.exception.code, "TAREFA_INEXISTENTE")

    def test_21_recusa_tipo_dependencia_invalido(self) -> None:
        doc = _base_doc()
        doc["dependencias"][0]["tipo"] = "XX"
        with self.assertRaises(CronogramaError) as ctx:
            parse_project(doc)
        self.assertEqual(ctx.exception.code, "DEPENDENCIA_INVALIDA")

    def test_22_recusa_defasagem_fora_do_intervalo(self) -> None:
        for lag in (-366, 366):
            doc = _base_doc()
            doc["dependencias"][0]["defasagem_dias"] = lag
            with self.assertRaises(CronogramaError) as ctx:
                parse_project(doc)
            self.assertEqual(ctx.exception.code, "DEPENDENCIA_INVALIDA")

    def test_23_recusa_data_civil_invalida_ou_nao_bissexta(self) -> None:
        for data_inv in ("2026-02-29", "2026-13-01", "02/03/2026", "2026-3-2"):
            doc = _base_doc()
            doc["projeto"]["data_inicio"] = data_inv
            with self.assertRaises(CronogramaError) as ctx:
                parse_project(doc)
            self.assertEqual(ctx.exception.code, "DATA_INVALIDA")

    def test_24_recusa_calendario_sem_dias_uteis_ou_repetido(self) -> None:
        doc1 = _base_doc()
        doc1["calendario"]["dias_uteis_semana"] = []
        with self.assertRaises(CronogramaError) as ctx1:
            parse_project(doc1)
        self.assertEqual(ctx1.exception.code, "CALENDARIO_INVALIDO")

        doc2 = _base_doc()
        doc2["calendario"]["dias_uteis_semana"] = [1, 2, 2]
        with self.assertRaises(CronogramaError) as ctx2:
            parse_project(doc2)
        self.assertEqual(ctx2.exception.code, "CALENDARIO_INVALIDO")

        doc3 = _base_doc()
        doc3["calendario"]["feriados"] = ["2026-03-09", "2026-03-09"]
        with self.assertRaises(CronogramaError) as ctx3:
            parse_project(doc3)
        self.assertEqual(ctx3.exception.code, "CALENDARIO_INVALIDO")


class TestRelacoesPrecedenciaCPM(unittest.TestCase):
    """Testa a matemática exata das relações FS, SS, FF e SF com defasagens."""

    def test_25_relacao_fs_com_defasagem_positiva_zero_e_negativa(self) -> None:
        doc = _base_doc()
        doc["tarefas"] = [
            {"id": "A", "nome": "A", "duracao_dias": 5, "setor": "S"},
            {"id": "B", "nome": "B", "duracao_dias": 3, "setor": "S"},
            {"id": "C", "nome": "C", "duracao_dias": 4, "setor": "S"},
        ]
        doc["dependencias"] = [
            {"id": "D1", "origem": "A", "destino": "B", "tipo": "FS", "defasagem_dias": 2},
            {"id": "D2", "origem": "B", "destino": "C", "tipo": "FS", "defasagem_dias": -1},
        ]
        res = plan_schedule(doc)
        por_id = {t.id: t for t in res.tarefas}
        self.assertEqual((por_id["A"].es, por_id["A"].ef), (0, 5))
        self.assertEqual((por_id["B"].es, por_id["B"].ef), (7, 10))
        self.assertEqual((por_id["C"].es, por_id["C"].ef), (9, 13))
        self.assertEqual(res.duracao_total_dias, 13)

    def test_26_relacao_ss_inicio_inicio(self) -> None:
        doc = _base_doc()
        doc["tarefas"] = [
            {"id": "A", "nome": "A", "duracao_dias": 6, "setor": "S"},
            {"id": "B", "nome": "B", "duracao_dias": 5, "setor": "S"},
        ]
        doc["dependencias"] = [
            {"id": "D1", "origem": "A", "destino": "B", "tipo": "SS", "defasagem_dias": 2},
        ]
        res = plan_schedule(doc)
        por_id = {t.id: t for t in res.tarefas}
        self.assertEqual((por_id["A"].es, por_id["A"].ef), (0, 6))
        self.assertEqual((por_id["B"].es, por_id["B"].ef), (2, 7))
        self.assertEqual(res.duracao_total_dias, 7)
        self.assertTrue(por_id["A"].critica)
        self.assertTrue(por_id["B"].critica)

    def test_27_relacao_ff_termino_termino(self) -> None:
        doc = _base_doc()
        doc["tarefas"] = [
            {"id": "A", "nome": "A", "duracao_dias": 5, "setor": "S"},
            {"id": "B", "nome": "B", "duracao_dias": 3, "setor": "S"},
        ]
        doc["dependencias"] = [
            {"id": "D1", "origem": "A", "destino": "B", "tipo": "FF", "defasagem_dias": 2},
        ]
        res = plan_schedule(doc)
        por_id = {t.id: t for t in res.tarefas}
        # EF(B) >= EF(A) + 2 = 7 => ES(B) = 7 - 3 = 4, EF(B) = 7
        self.assertEqual((por_id["B"].es, por_id["B"].ef), (4, 7))
        self.assertEqual(res.duracao_total_dias, 7)
        self.assertEqual(por_id["A"].folga_total, 0)
        self.assertEqual(por_id["B"].folga_total, 0)

    def test_28_relacao_sf_inicio_termino(self) -> None:
        doc = _base_doc()
        doc["tarefas"] = [
            {"id": "A", "nome": "A", "duracao_dias": 4, "setor": "S"},
            {"id": "B", "nome": "B", "duracao_dias": 3, "setor": "S"},
            {"id": "C", "nome": "C", "duracao_dias": 2, "setor": "S"},
        ]
        doc["dependencias"] = [
            {"id": "D1", "origem": "A", "destino": "B", "tipo": "FS", "defasagem_dias": 0},
            # EF(C) >= ES(B) + 3 = 4 + 3 = 7 => ES(C) = 7 - 2 = 5, EF(C) = 7
            {"id": "D2", "origem": "B", "destino": "C", "tipo": "SF", "defasagem_dias": 3},
        ]
        res = plan_schedule(doc)
        por_id = {t.id: t for t in res.tarefas}
        self.assertEqual((por_id["B"].es, por_id["B"].ef), (4, 7))
        self.assertEqual((por_id["C"].es, por_id["C"].ef), (5, 7))
        self.assertEqual(res.duracao_total_dias, 7)

    def test_29_recusa_defasagem_que_forca_es_menor_que_zero(self) -> None:
        doc = _base_doc()
        doc["dependencias"] = [
            {"id": "DEP-NEG", "origem": "T1", "destino": "T2", "tipo": "SS", "defasagem_dias": -1},
        ]
        with self.assertRaises(CronogramaError) as ctx:
            plan_schedule(doc)
        self.assertEqual(ctx.exception.code, "DATA_ANTERIOR_AO_INICIO")
        self.assertEqual(ctx.exception.dependencia_id, "DEP-NEG")
        self.assertIn("DEP-NEG", ctx.exception.message)

    def test_30_recusa_ff_que_forca_es_menor_que_zero(self) -> None:
        doc = _base_doc()
        doc["tarefas"] = [
            {"id": "T1", "nome": "Curta", "duracao_dias": 2, "setor": "S"},
            {"id": "T2", "nome": "Longa", "duracao_dias": 6, "setor": "S"},
        ]
        doc["dependencias"] = [
            {"id": "DEP-FF-NEG", "origem": "T1", "destino": "T2", "tipo": "FF", "defasagem_dias": 0},
        ]
        with self.assertRaises(CronogramaError) as ctx:
            plan_schedule(doc)
        self.assertEqual(ctx.exception.code, "DATA_ANTERIOR_AO_INICIO")
        self.assertEqual(ctx.exception.dependencia_id, "DEP-FF-NEG")

    def test_31_marcos_duracao_zero_inicio_meio_e_fim(self) -> None:
        doc = _base_doc()
        doc["tarefas"] = [
            {"id": "M0", "nome": "Marco Inicial", "duracao_dias": 0, "setor": "G"},
            {"id": "T1", "nome": "Execução", "duracao_dias": 4, "setor": "G"},
            {"id": "M1", "nome": "Marco Intermediário", "duracao_dias": 0, "setor": "G"},
            {"id": "T2", "nome": "Validação", "duracao_dias": 2, "setor": "G"},
            {"id": "M2", "nome": "Marco Final", "duracao_dias": 0, "setor": "G"},
        ]
        doc["dependencias"] = [
            {"id": "D1", "origem": "M0", "destino": "T1", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D2", "origem": "T1", "destino": "M1", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D3", "origem": "M1", "destino": "T2", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D4", "origem": "T2", "destino": "M2", "tipo": "FS", "defasagem_dias": 0},
        ]
        res = plan_schedule(doc)
        por_id = {t.id: t for t in res.tarefas}
        self.assertTrue(por_id["M0"].marco)
        self.assertTrue(por_id["M1"].marco)
        self.assertTrue(por_id["M2"].marco)
        self.assertEqual(por_id["M0"].data_inicio_cedo, "2026-03-02")
        self.assertEqual(por_id["M1"].data_fim_cedo, "2026-03-05")
        self.assertEqual(por_id["M2"].data_fim_cedo, "2026-03-09")
        self.assertEqual(res.caminhos_criticos[0].tarefas, ("M0", "T1", "M1", "T2", "M2"))

    def test_32_projeto_apenas_com_marco_zero_dias(self) -> None:
        doc = _base_doc()
        doc["tarefas"] = [
            {"id": "M0", "nome": "Marco Único", "duracao_dias": 0, "setor": "G"},
        ]
        doc["dependencias"] = []
        res = plan_schedule(doc)
        self.assertEqual(res.duracao_total_dias, 0)
        self.assertEqual(res.data_inicio_util, "2026-03-02")
        self.assertEqual(res.data_fim_planejada, "2026-03-02")
        self.assertEqual(len(res.caminhos_criticos), 1)
        self.assertEqual(res.caminhos_criticos[0].tarefas, ("M0",))


class TestFolgasECaminhosCriticos(unittest.TestCase):
    """Testa folga total, folga livre e enumeração de múltiplos caminhos críticos."""

    def test_33_diferenca_entre_folga_total_e_folga_livre(self) -> None:
        doc = _base_doc()
        doc["tarefas"] = [
            {"id": "A", "nome": "Crítica Longa", "duracao_dias": 10, "setor": "S"},
            {"id": "B", "nome": "Ramo Folga 1", "duracao_dias": 3, "setor": "S"},
            {"id": "C", "nome": "Ramo Folga 2", "duracao_dias": 2, "setor": "S"},
            {"id": "D", "nome": "Convergência", "duracao_dias": 2, "setor": "S"},
        ]
        doc["dependencias"] = [
            {"id": "D1", "origem": "A", "destino": "D", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D2", "origem": "B", "destino": "C", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D3", "origem": "C", "destino": "D", "tipo": "FS", "defasagem_dias": 0},
        ]
        res = plan_schedule(doc)
        por_id = {t.id: t for t in res.tarefas}
        # Ramo B->C tem 5 dias vs 10 dias de A => FT(B)=5, FL(B)=0; FT(C)=5, FL(C)=5
        self.assertEqual(por_id["B"].folga_total, 5)
        self.assertEqual(por_id["B"].folga_livre, 0)
        self.assertEqual(por_id["C"].folga_total, 5)
        self.assertEqual(por_id["C"].folga_livre, 5)

    def test_34_quatro_caminhos_criticos_empatados_em_diamante_duplo(self) -> None:
        doc = _base_doc()
        doc["tarefas"] = [
            {"id": "I", "nome": "Início", "duracao_dias": 1, "setor": "S"},
            {"id": "A1", "nome": "A1", "duracao_dias": 3, "setor": "S"},
            {"id": "A2", "nome": "A2", "duracao_dias": 3, "setor": "S"},
            {"id": "M", "nome": "Meio", "duracao_dias": 1, "setor": "S"},
            {"id": "B1", "nome": "B1", "duracao_dias": 4, "setor": "S"},
            {"id": "B2", "nome": "B2", "duracao_dias": 4, "setor": "S"},
            {"id": "F", "nome": "Fim", "duracao_dias": 1, "setor": "S"},
        ]
        doc["dependencias"] = [
            {"id": "D1", "origem": "I", "destino": "A1", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D2", "origem": "I", "destino": "A2", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D3", "origem": "A1", "destino": "M", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D4", "origem": "A2", "destino": "M", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D5", "origem": "M", "destino": "B1", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D6", "origem": "M", "destino": "B2", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D7", "origem": "B1", "destino": "F", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D8", "origem": "B2", "destino": "F", "tipo": "FS", "defasagem_dias": 0},
        ]
        res = plan_schedule(doc)
        self.assertEqual(len(res.caminhos_criticos), 4)
        esperados = [
            ("I", "A1", "M", "B1", "F"),
            ("I", "A1", "M", "B2", "F"),
            ("I", "A2", "M", "B1", "F"),
            ("I", "A2", "M", "B2", "F"),
        ]
        self.assertEqual([c.tarefas for c in res.caminhos_criticos], esperados)

        # Se orcamento_caminhos = 3, deve recusar com ORCAMENTO_CAMINHOS_EXCEDIDO
        with self.assertRaises(CronogramaError) as ctx:
            plan_schedule(doc, orcamento_caminhos=3)
        self.assertEqual(ctx.exception.code, "ORCAMENTO_CAMINHOS_EXCEDIDO")
        self.assertEqual(ctx.exception.detalhes["total_caminhos_criticos"], 4)


class TestDeteccaoDeCiclos(unittest.TestCase):
    """Testa detecção determinística de auto-laços e ciclos dirigidos."""

    def test_35_auto_laco_detectado(self) -> None:
        doc = _base_doc()
        doc["dependencias"] = [
            {"id": "D1", "origem": "T1", "destino": "T1", "tipo": "FS", "defasagem_dias": 0},
        ]
        with self.assertRaises(CronogramaError) as ctx:
            plan_schedule(doc)
        self.assertEqual(ctx.exception.code, "CICLO_DETECTADO")
        self.assertEqual(ctx.exception.ciclo, ("T1", "T1"))

    def test_36_ciclo_tres_tarefas_rotacionado_canonicamente(self) -> None:
        doc = _base_doc()
        doc["tarefas"] = [
            {"id": "T03", "nome": "3", "duracao_dias": 1, "setor": "S"},
            {"id": "T01", "nome": "1", "duracao_dias": 1, "setor": "S"},
            {"id": "T02", "nome": "2", "duracao_dias": 1, "setor": "S"},
        ]
        doc["dependencias"] = [
            {"id": "D1", "origem": "T03", "destino": "T01", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D2", "origem": "T01", "destino": "T02", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D3", "origem": "T02", "destino": "T03", "tipo": "FS", "defasagem_dias": 0},
        ]
        with self.assertRaises(CronogramaError) as ctx:
            plan_schedule(doc)
        self.assertEqual(ctx.exception.code, "CICLO_DETECTADO")
        self.assertEqual(ctx.exception.ciclo, ("T01", "T02", "T03", "T01"))

    def test_37_ciclo_com_cauda_pendente_isola_apenas_o_ciclo(self) -> None:
        doc = _base_doc()
        doc["tarefas"] = [
            {"id": "INICIO", "nome": "Início", "duracao_dias": 1, "setor": "S"},
            {"id": "B", "nome": "B", "duracao_dias": 1, "setor": "S"},
            {"id": "C", "nome": "C", "duracao_dias": 1, "setor": "S"},
            {"id": "SUMIDOURO", "nome": "Sumidouro", "duracao_dias": 1, "setor": "S"},
        ]
        doc["dependencias"] = [
            {"id": "D1", "origem": "INICIO", "destino": "B", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D2", "origem": "B", "destino": "C", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D3", "origem": "C", "destino": "B", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D4", "origem": "C", "destino": "SUMIDOURO", "tipo": "FS", "defasagem_dias": 0},
        ]
        with self.assertRaises(CronogramaError) as ctx:
            plan_schedule(doc)
        self.assertEqual(ctx.exception.code, "CICLO_DETECTADO")
        self.assertEqual(ctx.exception.ciclo, ("B", "C", "B"))


class TestCalendarioCivilEBissexto(unittest.TestCase):
    """Testa calendário de dias úteis, feriados, fins de semana e anos bissextos."""

    def test_38_ano_bissexto_2024_com_feriado(self) -> None:
        doc = _base_doc()
        doc["projeto"]["data_inicio"] = "2024-02-27"
        doc["calendario"]["feriados"] = ["2024-02-28"]
        doc["tarefas"] = [
            {"id": "T1", "nome": "T1", "duracao_dias": 3, "setor": "S"},
        ]
        doc["dependencias"] = []
        res = plan_schedule(doc)
        # D00 = 2024-02-27 (terça), 2024-02-28 é feriado, D01 = 2024-02-29 (bissexto!), D02 = 2024-03-01 (sexta)
        self.assertEqual([d.data for d in res.mapa_dias_uteis], ["2024-02-27", "2024-02-29", "2024-03-01"])
        self.assertEqual(res.tarefas[0].data_inicio_cedo, "2024-02-27")
        self.assertEqual(res.tarefas[0].data_fim_cedo, "2024-03-01")

    def test_39_data_inicio_em_fim_de_semana_avanca_para_primeiro_dia_util(self) -> None:
        doc = _base_doc()
        doc["projeto"]["data_inicio"] = "2026-02-28"  # Sábado
        doc["tarefas"] = [{"id": "T1", "nome": "T1", "duracao_dias": 2, "setor": "S"}]
        doc["dependencias"] = []
        res = plan_schedule(doc)
        self.assertEqual(res.data_inicio_util, "2026-03-02")
        self.assertEqual(res.data_fim_planejada, "2026-03-03")

    def test_40_escala_6x1_segunda_a_sabado(self) -> None:
        doc = _base_doc()
        doc["projeto"]["data_inicio"] = "2026-03-06"  # Sexta-feira
        doc["calendario"]["dias_uteis_semana"] = [1, 2, 3, 4, 5, 6]
        doc["tarefas"] = [{"id": "T1", "nome": "T1", "duracao_dias": 2, "setor": "S"}]
        doc["dependencias"] = []
        res = plan_schedule(doc)
        # Sexta (06/03) e Sábado (07/03) são úteis
        self.assertEqual([d.data for d in res.mapa_dias_uteis], ["2026-03-06", "2026-03-07"])


class TestImutabilidadeECSV(unittest.TestCase):
    """Testa imutabilidade das estruturas e proteção contra fórmula CSV."""

    def test_41_imutabilidade_do_resultado_e_isolamento_da_entrada(self) -> None:
        doc = _base_doc()
        copia = copy.deepcopy(doc)
        res = plan_schedule(doc)
        doc["projeto"]["titulo"] = "Mutado Depois"
        doc["tarefas"].append({"id": "T99", "nome": "X", "duracao_dias": 1, "setor": "S"})
        self.assertEqual(res.source_document(), copia)
        with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
            res.tarefas[0].es = 999  # type: ignore[misc]

    def test_42_protecao_injecao_formula_csv(self) -> None:
        doc = _base_doc()
        doc["tarefas"] = [
            {"id": "T1", "nome": "=SUM(A1:A2)", "duracao_dias": 2, "setor": "+Setor"},
            {"id": "T2", "nome": "  - perigoso", "duracao_dias": 1, "setor": "@Admin"},
        ]
        raw = dumps(doc).encode("utf-8")
        res = plan_json(raw)
        pacote = build_bundle(res, raw)
        with zipfile.ZipFile(io.BytesIO(pacote)) as zf:
            linhas = list(csv.reader(io.StringIO(zf.read("tarefas.csv").decode("utf-8"))))
            cron = loads(zf.read("cronograma.json"))
        self.assertEqual(linhas[1][1], "'=SUM(A1:A2)")
        self.assertEqual(linhas[1][2], "'+Setor")
        self.assertEqual(linhas[2][1], "'  - perigoso")
        self.assertEqual(linhas[2][2], "'@Admin")
        # No JSON os textos originais permanecem intactos
        self.assertEqual(cron["tarefas"][0]["nome"], "=SUM(A1:A2)")
        self.assertEqual(cron["tarefas"][1]["setor"], "@Admin")


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli_main(argv)
    return rc, out.getvalue(), err.getvalue()


class TestPacoteCLIEDistribuicao(unittest.TestCase):
    """Testa a CLI, não-sobrescrita, determinismo do ZIP e verificador independente."""

    def test_43_cli_exemplo_deterministico_byte_a_byte(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            z1 = Path(tmp) / "p1.zip"
            z2 = Path(tmp) / "p2.zip"
            self.assertEqual(_run_cli(["exemplo", "--saida", str(z1)])[0], 0)
            self.assertEqual(_run_cli(["exemplo", "--saida", str(z2)])[0], 0)
            self.assertEqual(z1.read_bytes(), z2.read_bytes())

    def test_44_cli_recusa_sobrescrever_arquivo_existente(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "existente.zip"
            destino.write_bytes(b"conteudo-original")
            rc, _, err = _run_cli(["exemplo", "--saida", str(destino)])
            self.assertEqual(rc, 2)
            self.assertIn("[DESTINO_EXISTENTE]", err)
            self.assertEqual(destino.read_bytes(), b"conteudo-original")

    def test_45_cli_recusa_sobrescrever_symlink_quebrado(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            alvo_inexistente = Path(tmp) / "nao-existe.zip"
            link = Path(tmp) / "atalho.zip"
            link.symlink_to(alvo_inexistente)
            rc, _, err = _run_cli(["exemplo", "--saida", str(link)])
            self.assertEqual(rc, 2)
            self.assertIn("[DESTINO_EXISTENTE]", err)
            self.assertFalse(alvo_inexistente.exists())

    def test_46_cli_recusa_entrada_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real.json"
            real.write_text(dumps(_base_doc()), encoding="utf-8")
            atalho = Path(tmp) / "link.json"
            atalho.symlink_to(real)
            saida = Path(tmp) / "saida.zip"
            rc, _, err = _run_cli(["planejar", str(atalho), "--saida", str(saida)])
            self.assertEqual(rc, 2)
            self.assertIn("[ARQUIVO_ENTRADA]", err)
            self.assertFalse(saida.exists())

    def test_47_cli_recusa_extensao_diferente_de_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            saida = Path(tmp) / "saida.tar"
            rc, _, err = _run_cli(["exemplo", "--saida", str(saida)])
            self.assertEqual(rc, 2)
            self.assertIn("[DESTINO_INVALIDO]", err)

    def test_48_cli_orcamento_caminhos_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entrada = Path(tmp) / "in.json"
            saida = Path(tmp) / "out.zip"
            doc = json.loads((ROOT / "src/cronograma_critico/data/exemplo.json").read_text(encoding="utf-8"))
            entrada.write_text(dumps(doc), encoding="utf-8")
            # Exemplo tem 2 caminhos críticos; com --orcamento-caminhos 1 deve recusar
            rc, _, err = _run_cli(["planejar", str(entrada), "--saida", str(saida), "--orcamento-caminhos", "1"])
            self.assertEqual(rc, 2)
            self.assertIn("[ORCAMENTO_CAMINHOS_EXCEDIDO]", err)
            self.assertFalse(saida.exists())

    def test_49_build_bundle_recusa_bytes_divergentes(self) -> None:
        doc = _base_doc()
        res = plan_schedule(doc)
        outro = _base_doc()
        outro["projeto"]["titulo"] = "Outro Título"
        with self.assertRaises(CronogramaError) as ctx:
            build_bundle(res, dumps(outro).encode("utf-8"))
        self.assertEqual(ctx.exception.code, "FONTE_RELATORIO")

    def test_50_verificador_independente_valida_exemplo_autoral(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "exemplo.zip"
            self.assertEqual(_run_cli(["exemplo", "--saida", str(zip_path)])[0], 0)
            proc = subprocess.run(
                [sys.executable, str(ROOT / "tools/verify_independent.py"), str(zip_path), "--demo"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            relatorio = json.loads(proc.stdout)
            self.assertTrue(relatorio["ok"])
            self.assertEqual(relatorio["resumo"]["duracao_total_dias"], 18)

    def test_51_svg_contem_todas_as_tarefas_e_dependencias_do_exemplo(self) -> None:
        raw = (ROOT / "src/cronograma_critico/data/exemplo.json").read_bytes()
        res = plan_json(raw)
        pacote = build_bundle(res, raw)
        with zipfile.ZipFile(io.BytesIO(pacote)) as zf:
            self.assertEqual(tuple(zf.namelist()), ARQUIVOS_PACOTE)
            svg_root = ET.fromstring(zf.read("gantt-cpm.svg").decode("utf-8"))
        ns = {"s": "http://www.w3.org/2000/svg"}
        tarefas_svg = [n.get("data-task-id") for n in svg_root.findall(".//s:g[@class='task-row']", ns)]
        deps_svg = [n.get("data-dep-id") for n in svg_root.findall(".//s:path[@class='dep-edge']", ns)]
        self.assertEqual(len(tarefas_svg), 12)
        self.assertEqual(len(deps_svg), 15)

    def test_52_plan_and_write_atalho_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "api.zip"
            raw = dumps(_base_doc()).encode("utf-8")
            res = plan_and_write(raw, destino)
            self.assertEqual(res.duracao_total_dias, 7)
            self.assertTrue(destino.is_file())

    def test_53_versao_publica_consistente(self) -> None:
        self.assertEqual(__version__, "1.0.0")

    def test_54_cli_argumentos_invalidos_retorna_2(self) -> None:
        self.assertEqual(_run_cli(["comando-inexistente"])[0], 2)
        self.assertEqual(_run_cli(["planejar", "a.json", "--saida", "b.zip", "--orcamento-caminhos", "0"])[0], 2)

    def test_55_dependencias_paralelas_tipos_diferentes_entre_mesmas_tarefas(self) -> None:
        doc = _base_doc()
        doc["tarefas"] = [
            {"id": "T1", "nome": "Tarefa 1", "duracao_dias": 4, "setor": "S"},
            {"id": "T2", "nome": "Tarefa 2", "duracao_dias": 4, "setor": "S"},
        ]
        doc["dependencias"] = [
            {"id": "D1", "origem": "T1", "destino": "T2", "tipo": "SS", "defasagem_dias": 2},
            {"id": "D2", "origem": "T1", "destino": "T2", "tipo": "FF", "defasagem_dias": 2},
        ]
        res = plan_schedule(doc)
        self.assertEqual(res.duracao_total_dias, 6)
        self.assertEqual(len(res.caminhos_criticos), 1)
        self.assertEqual(res.caminhos_criticos[0].tarefas, ("T1", "T2"))
        self.assertEqual(res.caminhos_criticos[0].dependencias, ("D1",))

    def test_56_sha256sums_interno_bate_com_todos_os_arquivos(self) -> None:
        import hashlib

        raw = (ROOT / "src/cronograma_critico/data/exemplo.json").read_bytes()
        pacote = build_bundle(plan_json(raw), raw)
        with zipfile.ZipFile(io.BytesIO(pacote)) as zf:
            linhas = zf.read("SHA256SUMS").decode("utf-8").splitlines()
            self.assertEqual(len(linhas), 7)
            for linha in linhas:
                digest, nome = linha.split("  ", 1)
                self.assertEqual(hashlib.sha256(zf.read(nome)).hexdigest(), digest)

    def test_57_recusa_diretorio_saida_inexistente(self) -> None:
        doc = _base_doc()
        raw = dumps(doc).encode("utf-8")
        res = plan_json(raw)
        with self.assertRaises(CronogramaError) as ctx:
            write_bundle("/caminho/que/nao/existe/pacote.zip", res, raw)
        self.assertEqual(ctx.exception.code, "DIRETORIO_SAIDA_INEXISTENTE")

    def test_58_recusa_texto_com_caracteres_de_controle(self) -> None:
        doc = _base_doc()
        doc["tarefas"][0]["nome"] = "Tarefa\x07Inválida"
        with self.assertRaises(CronogramaError) as ctx:
            parse_project(doc)
        self.assertEqual(ctx.exception.code, "ESQUEMA_INVALIDO")

    def test_59_recusa_texto_apenas_espacos(self) -> None:
        doc = _base_doc()
        doc["tarefas"][0]["setor"] = "    "
        with self.assertRaises(CronogramaError) as ctx:
            parse_project(doc)
        self.assertEqual(ctx.exception.code, "ESQUEMA_INVALIDO")

    def test_60_recusa_ano_fora_da_faixa_1970_2099(self) -> None:
        for data_fora in ("1969-12-31", "2100-01-01"):
            doc = _base_doc()
            doc["projeto"]["data_inicio"] = data_fora
            with self.assertRaises(CronogramaError) as ctx:
                parse_project(doc)
            self.assertEqual(ctx.exception.code, "DATA_INVALIDA")

    def test_61_ordem_topologica_deterministica_independente_da_ordem_de_entrada(self) -> None:
        doc1 = _base_doc()
        doc1["tarefas"] = [
            {"id": "T03", "nome": "3", "duracao_dias": 2, "setor": "S"},
            {"id": "T01", "nome": "1", "duracao_dias": 2, "setor": "S"},
            {"id": "T02", "nome": "2", "duracao_dias": 2, "setor": "S"},
        ]
        doc1["dependencias"] = [
            {"id": "D2", "origem": "T01", "destino": "T03", "tipo": "FS", "defasagem_dias": 0},
            {"id": "D1", "origem": "T01", "destino": "T02", "tipo": "FS", "defasagem_dias": 0},
        ]
        doc2 = copy.deepcopy(doc1)
        doc2["tarefas"].reverse()
        doc2["dependencias"].reverse()
        r1 = plan_schedule(doc1)
        r2 = plan_schedule(doc2)
        self.assertEqual(r1.ordem_topologica, ("T01", "T02", "T03"))
        self.assertEqual(r2.ordem_topologica, ("T01", "T02", "T03"))
        self.assertEqual(r1.caminhos_criticos, r2.caminhos_criticos)

    def test_62_relatorio_markdown_contem_secoes_obrigatorias(self) -> None:
        raw = (ROOT / "src/cronograma_critico/data/exemplo.json").read_bytes()
        pacote = build_bundle(plan_json(raw), raw)
        with zipfile.ZipFile(io.BytesIO(pacote)) as zf:
            md = zf.read("RELATORIO.md").decode("utf-8")
        for secao in (
            "## 1. Resumo Executivo",
            "## 2. Caminhos Críticos Determinísticos",
            "## 3. Quadro de Tarefas",
            "## 4. Ligações de Precedência",
            "## 5. Calendário de Dias Úteis",
            "CAMINHO-01",
            "CAMINHO-02",
        ):
            self.assertIn(secao, md)

    def test_63_marco_nao_critico_com_folga_total_no_svg(self) -> None:
        doc = _base_doc()
        doc["tarefas"] = [
            {"id": "T1", "nome": "Longa", "duracao_dias": 5, "setor": "S"},
            {"id": "M1", "nome": "Marco Folgado", "duracao_dias": 0, "setor": "S"},
        ]
        doc["dependencias"] = []
        raw = dumps(doc).encode("utf-8")
        pacote = build_bundle(plan_json(raw), raw)
        with zipfile.ZipFile(io.BytesIO(pacote)) as zf:
            svg = zf.read("gantt-cpm.svg").decode("utf-8")
        self.assertIn('data-task-id="M1" data-critica="false" data-marco="true"', svg)

    def test_64_detalhes_de_erro_sao_imutaveis(self) -> None:
        doc = _base_doc()
        doc["dependencias"] = [
            {"id": "D1", "origem": "T1", "destino": "T1", "tipo": "FS", "defasagem_dias": 0}
        ]
        with self.assertRaises(CronogramaError) as ctx:
            plan_schedule(doc)
        with self.assertRaises(TypeError):
            ctx.exception.detalhes["ciclo"] = ("X",)  # type: ignore[index]

    def test_65_entrada_inexistente_na_cli_retorna_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, err = _run_cli(["planejar", str(Path(tmp) / "ausente.json"), "--saida", str(Path(tmp) / "s.zip")])
            self.assertEqual(rc, 2)
            self.assertIn("[LEITURA_ENTRADA]", err)

    def test_66_execucao_modulo_python_m_cronograma_critico_version(self) -> None:
        env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONUTF8="1")
        proc = subprocess.run(
            [sys.executable, "-m", "cronograma_critico", "--version"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("cronograma-critico 1.0.0", proc.stdout)


if __name__ == "__main__":
    unittest.main()
