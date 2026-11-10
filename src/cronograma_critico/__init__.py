"""Planejamento determinístico por Método do Caminho Crítico (CPM)."""
from __future__ import annotations

__version__ = "1.0.0"

from .core import (
    CaminhoCritico,
    DependenciaCalculada,
    DependenciaEntrada,
    DiaUtilMapa,
    ProjetoEntrada,
    ResultadoCronograma,
    TarefaCalculada,
    TarefaEntrada,
    parse_project,
    plan_json,
    plan_schedule,
)
from .errors import CronogramaError
from .jsoncodec import dumps, dumps_output, loads
from .reports import ARQUIVOS_PACOTE, build_bundle, plan_and_write, write_bundle

__all__ = [
    "ARQUIVOS_PACOTE",
    "CaminhoCritico",
    "CronogramaError",
    "DependenciaCalculada",
    "DependenciaEntrada",
    "DiaUtilMapa",
    "ProjetoEntrada",
    "ResultadoCronograma",
    "TarefaCalculada",
    "TarefaEntrada",
    "__version__",
    "build_bundle",
    "dumps",
    "dumps_output",
    "loads",
    "parse_project",
    "plan_and_write",
    "plan_json",
    "plan_schedule",
    "write_bundle",
]
