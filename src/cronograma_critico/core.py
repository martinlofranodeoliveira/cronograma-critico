"""Motor determinístico de Caminho Crítico (CPM) com relações FS/SS/FF/SF."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import heapq
import re
from types import MappingProxyType
from typing import Any, Mapping

from . import __version__
from .errors import CronogramaError
from .jsoncodec import loads

MAX_TAREFAS = 1_000
MAX_DEPENDENCIAS = 5_000
MAX_DURACAO_DIAS = 3_650
MIN_DEFASAGEM_DIAS = -365
MAX_DEFASAGEM_DIAS = 365
MAX_FERIADOS = 1_000
MAX_ORCAMENTO_CAMINHOS = 10_000
PADRAO_ORCAMENTO_CAMINHOS = 1_000

TIPOS_DEPENDENCIA = ("FS", "SS", "FF", "SF")
_ID_ASCII = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_DATA_ISO = re.compile(r"\d{4}-\d{2}-\d{2}\Z")

_CAMPOS_RAIZ = frozenset(
    {"schema_version", "projeto", "calendario", "tarefas", "dependencias", "orcamento_caminhos"}
)
_CAMPOS_RAIZ_OBRIGATORIOS = frozenset(
    {"schema_version", "projeto", "calendario", "tarefas", "dependencias"}
)
_CAMPOS_PROJETO = frozenset({"id", "titulo", "data_inicio"})
_CAMPOS_CALENDARIO = frozenset({"dias_uteis_semana", "feriados"})
_CAMPOS_TAREFA = frozenset({"id", "nome", "duracao_dias", "setor"})
_CAMPOS_DEPENDENCIA = frozenset({"id", "origem", "destino", "tipo", "defasagem_dias"})


@dataclass(frozen=True)
class TarefaEntrada:
    id: str
    nome: str
    duracao_dias: int
    setor: str


@dataclass(frozen=True)
class DependenciaEntrada:
    id: str
    origem: str
    destino: str
    tipo: str
    defasagem_dias: int


@dataclass(frozen=True)
class ProjetoEntrada:
    schema_version: int
    id: str
    titulo: str
    data_inicio: str
    dias_uteis_semana: tuple[int, ...]
    feriados: tuple[str, ...]
    orcamento_caminhos: int
    tarefas: tuple[TarefaEntrada, ...]
    dependencias: tuple[DependenciaEntrada, ...]


@dataclass(frozen=True)
class DiaUtilMapa:
    indice: int
    data: str
    dia_semana_iso: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "indice": self.indice,
            "data": self.data,
            "dia_semana_iso": self.dia_semana_iso,
        }


@dataclass(frozen=True)
class TarefaCalculada:
    id: str
    nome: str
    setor: str
    duracao_dias: int
    marco: bool
    es: int
    ef: int
    ls: int
    lf: int
    folga_total: int
    folga_livre: int
    critica: bool
    data_inicio_cedo: str
    data_fim_cedo: str
    data_inicio_tarde: str
    data_fim_tarde: str
    predecessoras: tuple[str, ...]
    sucessoras: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nome": self.nome,
            "setor": self.setor,
            "duracao_dias": self.duracao_dias,
            "marco": self.marco,
            "es": self.es,
            "ef": self.ef,
            "ls": self.ls,
            "lf": self.lf,
            "folga_total": self.folga_total,
            "folga_livre": self.folga_livre,
            "critica": self.critica,
            "data_inicio_cedo": self.data_inicio_cedo,
            "data_fim_cedo": self.data_fim_cedo,
            "data_inicio_tarde": self.data_inicio_tarde,
            "data_fim_tarde": self.data_fim_tarde,
            "predecessoras": list(self.predecessoras),
            "sucessoras": list(self.sucessoras),
        }


@dataclass(frozen=True)
class DependenciaCalculada:
    id: str
    origem: str
    destino: str
    tipo: str
    defasagem_dias: int
    deslocamento_es_dias: int
    es_minimo_destino: int
    folga_ligacao: int
    critica: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "origem": self.origem,
            "destino": self.destino,
            "tipo": self.tipo,
            "defasagem_dias": self.defasagem_dias,
            "deslocamento_es_dias": self.deslocamento_es_dias,
            "es_minimo_destino": self.es_minimo_destino,
            "folga_ligacao": self.folga_ligacao,
            "critica": self.critica,
        }


@dataclass(frozen=True)
class CaminhoCritico:
    indice: int
    id: str
    tarefas: tuple[str, ...]
    dependencias: tuple[str, ...]
    quantidade_tarefas: int
    duracao_total_dias: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "indice": self.indice,
            "id": self.id,
            "tarefas": list(self.tarefas),
            "dependencias": list(self.dependencias),
            "quantidade_tarefas": self.quantidade_tarefas,
            "duracao_total_dias": self.duracao_total_dias,
        }


@dataclass(frozen=True)
class ResultadoCronograma:
    entrada: ProjetoEntrada
    data_inicio_util: str
    data_fim_planejada: str
    duracao_total_dias: int
    orcamento_caminhos: int
    feriados_no_horizonte: tuple[str, ...]
    mapa_dias_uteis: tuple[DiaUtilMapa, ...]
    ordem_topologica: tuple[str, ...]
    tarefas_criticas: tuple[str, ...]
    tarefas: tuple[TarefaCalculada, ...]
    dependencias: tuple[DependenciaCalculada, ...]
    caminhos_criticos: tuple[CaminhoCritico, ...]
    _fonte_original: Mapping[str, Any]

    def source_document(self) -> dict[str, Any]:
        return _thaw(self._fonte_original)

    def to_dict(self) -> dict[str, Any]:
        total_marcos = sum(1 for t in self.tarefas if t.marco)
        total_criticas = len(self.tarefas_criticas)
        total_dep_criticas = sum(1 for d in self.dependencias if d.critica)
        return {
            "schema_version": 1,
            "ferramenta": {
                "nome": "cronograma-critico",
                "versao": __version__,
            },
            "projeto": {
                "id": self.entrada.id,
                "titulo": self.entrada.titulo,
                "data_inicio_declarada": self.entrada.data_inicio,
                "data_inicio_util": self.data_inicio_util,
                "data_fim_planejada": self.data_fim_planejada,
                "duracao_total_dias": self.duracao_total_dias,
                "orcamento_caminhos": self.orcamento_caminhos,
            },
            "calendario": {
                "dias_uteis_semana": list(self.entrada.dias_uteis_semana),
                "feriados": list(self.entrada.feriados),
                "feriados_no_horizonte": list(self.feriados_no_horizonte),
                "mapa_dias_uteis": [d.to_dict() for d in self.mapa_dias_uteis],
            },
            "resumo": {
                "total_tarefas": len(self.tarefas),
                "total_marcos": total_marcos,
                "total_tarefas_criticas": total_criticas,
                "total_tarefas_com_folga": len(self.tarefas) - total_criticas,
                "total_dependencias": len(self.dependencias),
                "total_dependencias_criticas": total_dep_criticas,
                "total_caminhos_criticos": len(self.caminhos_criticos),
                "duracao_total_dias": self.duracao_total_dias,
                "data_inicio_util": self.data_inicio_util,
                "data_fim_planejada": self.data_fim_planejada,
            },
            "ordem_topologica": list(self.ordem_topologica),
            "tarefas_criticas": list(self.tarefas_criticas),
            "tarefas": [t.to_dict() for t in self.tarefas],
            "dependencias": [d.to_dict() for d in self.dependencias],
            "caminhos_criticos": [c.to_dict() for c in self.caminhos_criticos],
        }


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, (dict, MappingProxyType)):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


def _require_mapping(obj: Any, path: str, permitidos: frozenset[str], obrigatorios: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(obj, (dict, MappingProxyType)):
        raise CronogramaError("ESQUEMA_INVALIDO", f"Esperado objeto JSON em {path}.", path=path)
    chaves = set(obj.keys())
    extras = sorted(chaves - permitidos)
    if extras:
        raise CronogramaError(
            "CAMPO_DESCONHECIDO",
            f"Campo desconhecido em {path}: {extras[0]!r}.",
            path=f"{path}.{extras[0]}",
        )
    faltantes = sorted(obrigatorios - chaves)
    if faltantes:
        raise CronogramaError(
            "ESQUEMA_INVALIDO",
            f"Campo obrigatório ausente em {path}: {faltantes[0]!r}.",
            path=f"{path}.{faltantes[0]}",
        )
    return obj


def _require_id(value: Any, path: str) -> str:
    if type(value) is not str or _ID_ASCII.fullmatch(value) is None:
        raise CronogramaError(
            "ESQUEMA_INVALIDO",
            f"Identificador inválido em {path}: use apenas ASCII [A-Za-z0-9._-] (1 a 64 caracteres).",
            path=path,
        )
    return value


def _require_text(value: Any, path: str, max_len: int = 160) -> str:
    if type(value) is not str:
        raise CronogramaError("ESQUEMA_INVALIDO", f"Texto obrigatório em {path}.", path=path)
    if any(ord(c) < 32 or ord(c) == 127 or (0xD800 <= ord(c) <= 0xDFFF) for c in value):
        raise CronogramaError("ESQUEMA_INVALIDO", f"Texto em {path} contém caracteres de controle inválidos.", path=path)
    limpo = value.strip()
    if not limpo or len(value) > max_len:
        raise CronogramaError(
            "ESQUEMA_INVALIDO",
            f"Texto em {path} deve ser não vazio e ter até {max_len} caracteres.",
            path=path,
        )
    return value


def _require_int(value: Any, path: str, minimo: int, maximo: int, code: str = "ESQUEMA_INVALIDO") -> int:
    if type(value) is bool:
        raise CronogramaError("ESQUEMA_INVALIDO", f"Booleano não é permitido como inteiro em {path}.", path=path)
    if type(value) is float:
        raise CronogramaError("NUMERO_NAO_INTEIRO", f"Float não é permitido em {path}.", path=path)
    if type(value) is not int:
        raise CronogramaError("ESQUEMA_INVALIDO", f"Esperado inteiro exato em {path}.", path=path)
    if not (minimo <= value <= maximo):
        raise CronogramaError(
            code,
            f"Inteiro em {path} fora do intervalo permitido [{minimo}, {maximo}]: {value}.",
            path=path,
        )
    return value


def _parse_iso_date(value: Any, path: str) -> date:
    if type(value) is not str or _DATA_ISO.fullmatch(value) is None:
        raise CronogramaError("DATA_INVALIDA", f"Data inválida em {path}; use formato YYYY-MM-DD.", path=path)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise CronogramaError("DATA_INVALIDA", f"Data civil inexistente em {path}: {value}.", path=path) from None
    if not (1970 <= parsed.year <= 2099):
        raise CronogramaError("DATA_INVALIDA", f"Ano fora da faixa suportada (1970..2099) em {path}.", path=path)
    return parsed


def parse_project(documento: Any, *, orcamento_caminhos_override: int | None = None) -> ProjetoEntrada:
    """Valida e converte o dicionário de entrada em estruturas imutáveis."""
    raiz = _require_mapping(documento, "raiz", _CAMPOS_RAIZ, _CAMPOS_RAIZ_OBRIGATORIOS)
    versao = raiz["schema_version"]
    if type(versao) is not int or versao != 1:
        raise CronogramaError(
            "VERSAO_ESQUEMA_INVALIDA",
            "Versão de esquema não suportada; esperado schema_version = 1.",
            path="raiz.schema_version",
        )

    orcamento_raw = raiz.get("orcamento_caminhos", PADRAO_ORCAMENTO_CAMINHOS)
    orcamento = _require_int(
        orcamento_raw, "raiz.orcamento_caminhos", 1, MAX_ORCAMENTO_CAMINHOS
    )
    if orcamento_caminhos_override is not None:
        orcamento = _require_int(
            orcamento_caminhos_override, "orcamento_caminhos", 1, MAX_ORCAMENTO_CAMINHOS
        )

    proj_obj = _require_mapping(raiz["projeto"], "projeto", _CAMPOS_PROJETO, _CAMPOS_PROJETO)
    proj_id = _require_id(proj_obj["id"], "projeto.id")
    proj_titulo = _require_text(proj_obj["titulo"], "projeto.titulo", max_len=160)
    _parse_iso_date(proj_obj["data_inicio"], "projeto.data_inicio")
    proj_data_inicio = proj_obj["data_inicio"]

    cal_obj = _require_mapping(raiz["calendario"], "calendario", _CAMPOS_CALENDARIO, _CAMPOS_CALENDARIO)
    dias_raw = cal_obj["dias_uteis_semana"]
    if not isinstance(dias_raw, (list, tuple)) or not (1 <= len(dias_raw) <= 7):
        raise CronogramaError(
            "CALENDARIO_INVALIDO",
            "calendario.dias_uteis_semana deve ser lista não vazia de inteiros ISO (1=segunda .. 7=domingo).",
            path="calendario.dias_uteis_semana",
        )
    dias_vistos: set[int] = set()
    dias_uteis: list[int] = []
    for idx, item in enumerate(dias_raw):
        dia = _require_int(item, f"calendario.dias_uteis_semana[{idx}]", 1, 7, code="CALENDARIO_INVALIDO")
        if dia in dias_vistos:
            raise CronogramaError(
                "CALENDARIO_INVALIDO",
                f"Dia da semana repetido em calendario.dias_uteis_semana: {dia}.",
                path=f"calendario.dias_uteis_semana[{idx}]",
            )
        dias_vistos.add(dia)
        dias_uteis.append(dia)

    feriados_raw = cal_obj["feriados"]
    if not isinstance(feriados_raw, (list, tuple)) or len(feriados_raw) > MAX_FERIADOS:
        raise CronogramaError(
            "CALENDARIO_INVALIDO",
            f"calendario.feriados deve ser lista de até {MAX_FERIADOS} datas YYYY-MM-DD.",
            path="calendario.feriados",
        )
    feriados_vistos: set[str] = set()
    feriados_lista: list[str] = []
    for idx, item in enumerate(feriados_raw):
        _parse_iso_date(item, f"calendario.feriados[{idx}]")
        if item in feriados_vistos:
            raise CronogramaError(
                "CALENDARIO_INVALIDO",
                f"Data de feriado duplicada em calendario.feriados: {item}.",
                path=f"calendario.feriados[{idx}]",
            )
        feriados_vistos.add(item)
        feriados_lista.append(item)

    tarefas_raw = raiz["tarefas"]
    if not isinstance(tarefas_raw, (list, tuple)) or not (1 <= len(tarefas_raw) <= MAX_TAREFAS):
        raise CronogramaError(
            "ESQUEMA_INVALIDO",
            f"tarefas deve conter entre 1 e {MAX_TAREFAS} tarefas.",
            path="tarefas",
        )
    tarefas_ids: set[str] = set()
    tarefas: list[TarefaEntrada] = []
    for idx, item in enumerate(tarefas_raw):
        t_path = f"tarefas[{idx}]"
        t_obj = _require_mapping(item, t_path, _CAMPOS_TAREFA, _CAMPOS_TAREFA)
        t_id = _require_id(t_obj["id"], f"{t_path}.id")
        if t_id in tarefas_ids:
            raise CronogramaError(
                "TAREFA_DUPLICADA",
                f"Identificador de tarefa duplicado: {t_id}.",
                path=f"{t_path}.id",
            )
        tarefas_ids.add(t_id)
        t_nome = _require_text(t_obj["nome"], f"{t_path}.nome", max_len=160)
        t_duracao = _require_int(t_obj["duracao_dias"], f"{t_path}.duracao_dias", 0, MAX_DURACAO_DIAS)
        t_setor = _require_text(t_obj["setor"], f"{t_path}.setor", max_len=80)
        tarefas.append(TarefaEntrada(id=t_id, nome=t_nome, duracao_dias=t_duracao, setor=t_setor))

    deps_raw = raiz["dependencias"]
    if not isinstance(deps_raw, (list, tuple)) or len(deps_raw) > MAX_DEPENDENCIAS:
        raise CronogramaError(
            "ESQUEMA_INVALIDO",
            f"dependencias deve ser lista de até {MAX_DEPENDENCIAS} ligações.",
            path="dependencias",
        )
    deps_ids: set[str] = set()
    deps_triplas: set[tuple[str, str, str]] = set()
    dependencias: list[DependenciaEntrada] = []
    for idx, item in enumerate(deps_raw):
        d_path = f"dependencias[{idx}]"
        d_obj = _require_mapping(item, d_path, _CAMPOS_DEPENDENCIA, _CAMPOS_DEPENDENCIA)
        d_id = _require_id(d_obj["id"], f"{d_path}.id")
        if d_id in deps_ids:
            raise CronogramaError(
                "DEPENDENCIA_DUPLICADA",
                f"Identificador de dependência duplicado: {d_id}.",
                path=f"{d_path}.id",
            )
        deps_ids.add(d_id)
        origem = _require_id(d_obj["origem"], f"{d_path}.origem")
        destino = _require_id(d_obj["destino"], f"{d_path}.destino")
        if origem not in tarefas_ids:
            raise CronogramaError(
                "TAREFA_INEXISTENTE",
                f"Dependência {d_id} referencia tarefa de origem inexistente: {origem}.",
                path=f"{d_path}.origem",
            )
        if destino not in tarefas_ids:
            raise CronogramaError(
                "TAREFA_INEXISTENTE",
                f"Dependência {d_id} referencia tarefa de destino inexistente: {destino}.",
                path=f"{d_path}.destino",
            )
        tipo = d_obj["tipo"]
        if type(tipo) is not str or tipo not in TIPOS_DEPENDENCIA:
            raise CronogramaError(
                "DEPENDENCIA_INVALIDA",
                f"Tipo de dependência inválido em {d_id}; use FS, SS, FF ou SF.",
                path=f"{d_path}.tipo",
            )
        tripla = (origem, destino, tipo)
        if tripla in deps_triplas:
            raise CronogramaError(
                "DEPENDENCIA_DUPLICADA",
                f"Dependência duplicada entre {origem} e {destino} com tipo {tipo}.",
                path=d_path,
            )
        deps_triplas.add(tripla)
        defasagem = _require_int(
            d_obj["defasagem_dias"],
            f"{d_path}.defasagem_dias",
            MIN_DEFASAGEM_DIAS,
            MAX_DEFASAGEM_DIAS,
            code="DEPENDENCIA_INVALIDA",
        )
        dependencias.append(
            DependenciaEntrada(
                id=d_id,
                origem=origem,
                destino=destino,
                tipo=tipo,
                defasagem_dias=defasagem,
            )
        )

    return ProjetoEntrada(
        schema_version=1,
        id=proj_id,
        titulo=proj_titulo,
        data_inicio=proj_data_inicio,
        dias_uteis_semana=tuple(sorted(dias_uteis)),
        feriados=tuple(sorted(feriados_lista)),
        orcamento_caminhos=orcamento,
        tarefas=tuple(tarefas),
        dependencias=tuple(dependencias),
    )


def _deslocamento_es(tipo: str, duracao_origem: int, duracao_destino: int, defasagem_dias: int) -> int:
    if tipo == "FS":
        return duracao_origem + defasagem_dias
    if tipo == "SS":
        return defasagem_dias
    if tipo == "FF":
        return duracao_origem + defasagem_dias - duracao_destino
    if tipo == "SF":
        return defasagem_dias - duracao_destino
    raise AssertionError(f"Tipo de dependência inesperado: {tipo}")


def _encontrar_ciclo_testemunha(
    ids_tarefas: tuple[str, ...],
    dependencias: tuple[DependenciaEntrada, ...],
    visitados_kahn: set[str],
) -> tuple[str, ...]:
    restantes = {t_id for t_id in ids_tarefas if t_id not in visitados_kahn}
    adj: dict[str, list[str]] = {
        t_id: sorted({d.destino for d in dependencias if d.origem == t_id and d.destino in restantes})
        for t_id in restantes
    }
    # Poda iterativa de sumidouros em restantes para manter apenas vértices que alcançam algum ciclo.
    alterou = True
    while alterou:
        alterou = False
        sem_saida = [u for u, vizinhos in adj.items() if not vizinhos]
        if sem_saida:
            alterou = True
            for u in sem_saida:
                restantes.remove(u)
                del adj[u]
            for u in list(adj):
                adj[u] = [v for v in adj[u] if v in restantes]

    estado: dict[str, int] = {u: 0 for u in restantes}
    for inicio in sorted(restantes):
        if estado[inicio] != 0:
            continue
        pilha: list[str] = [inicio]
        pos_pilha: dict[str, int] = {inicio: 0}
        indices_vizinho: dict[str, int] = {inicio: 0}
        estado[inicio] = 1

        while pilha:
            atual = pilha[-1]
            vizinhos = adj[atual]
            idx = indices_vizinho[atual]
            if idx < len(vizinhos):
                proximo = vizinhos[idx]
                indices_vizinho[atual] = idx + 1
                if estado[proximo] == 1:
                    corte = pilha[pos_pilha[proximo] :]
                    menor_idx = min(range(len(corte)), key=lambda i: corte[i])
                    rotacionado = corte[menor_idx:] + corte[:menor_idx]
                    return tuple(rotacionado + [rotacionado[0]])
                if estado[proximo] == 0:
                    estado[proximo] = 1
                    pos_pilha[proximo] = len(pilha)
                    pilha.append(proximo)
                    indices_vizinho[proximo] = 0
            else:
                estado[atual] = 2
                pilha.pop()
                del pos_pilha[atual]
    raise AssertionError("Esperado ciclo testemunha entre nós não ordenados topologicamente.")


def _ordenacao_topologica_e_ciclos(
    tarefas: tuple[TarefaEntrada, ...],
    dependencias: tuple[DependenciaEntrada, ...],
) -> tuple[str, ...]:
    # Verifica auto-laços primeiro em ordem determinística de ID da tarefa.
    auto_lacos = sorted(
        (d for d in dependencias if d.origem == d.destino),
        key=lambda d: (d.origem, d.id),
    )
    if auto_lacos:
        d = auto_lacos[0]
        ciclo = (d.origem, d.destino)
        raise CronogramaError(
            "CICLO_DETECTADO",
            f"Ciclo detectado no grafo de precedências: {d.origem} -> {d.destino}.",
            detalhes={"ciclo": ciclo, "dependencia_id": d.id},
        )

    ids_tarefas = tuple(sorted(t.id for t in tarefas))
    grau_entrada: dict[str, int] = {t_id: 0 for t_id in ids_tarefas}
    saida: dict[str, list[str]] = {t_id: [] for t_id in ids_tarefas}
    for d in dependencias:
        grau_entrada[d.destino] += 1
        saida[d.origem].append(d.destino)
    for t_id in ids_tarefas:
        saida[t_id].sort()

    fila: list[str] = [t_id for t_id in ids_tarefas if grau_entrada[t_id] == 0]
    heapq.heapify(fila)
    ordem: list[str] = []
    while fila:
        atual = heapq.heappop(fila)
        ordem.append(atual)
        for vizinho in saida[atual]:
            grau_entrada[vizinho] -= 1
            if grau_entrada[vizinho] == 0:
                heapq.heappush(fila, vizinho)

    if len(ordem) < len(ids_tarefas):
        ciclo = _encontrar_ciclo_testemunha(ids_tarefas, dependencias, set(ordem))
        raise CronogramaError(
            "CICLO_DETECTADO",
            f"Ciclo detectado no grafo de precedências: {' -> '.join(ciclo)}.",
            detalhes={"ciclo": ciclo},
        )

    return tuple(ordem)


def _construir_calendario(
    data_inicio_iso: str,
    dias_uteis_semana: tuple[int, ...],
    feriados_iso: tuple[str, ...],
    quantidade_dias_uteis: int,
) -> tuple[tuple[DiaUtilMapa, ...], tuple[str, ...]]:
    data_atual = date.fromisoformat(data_inicio_iso)
    dias_uteis_set = frozenset(dias_uteis_semana)
    feriados_set = frozenset(feriados_iso)
    necessarios = max(1, quantidade_dias_uteis)
    mapa: list[DiaUtilMapa] = []
    feriados_horizonte: list[str] = []
    um_dia = timedelta(days=1)

    passos = 0
    limite_passos = necessarios * 14 + len(feriados_iso) + 366
    while len(mapa) < necessarios:
        if passos > limite_passos or data_atual.year > 2200:
            raise CronogramaError(
                "CALENDARIO_INVALIDO",
                "Não foi possível alocar dias úteis suficientes no horizonte do calendário.",
            )
        iso = data_atual.isoformat()
        dia_semana = data_atual.isoweekday()
        if dia_semana in dias_uteis_set:
            if iso in feriados_set:
                if mapa:
                    feriados_horizonte.append(iso)
                else:
                    feriados_horizonte.append(iso)
            else:
                mapa.append(DiaUtilMapa(indice=len(mapa), data=iso, dia_semana_iso=dia_semana))
        data_atual += um_dia
        passos += 1

    return tuple(mapa), tuple(feriados_horizonte)


def _enumerar_caminhos_criticos(
    tarefas_criticas: tuple[str, ...],
    dependencias_criticas: tuple[DependenciaCalculada, ...],
    duracao_total_dias: int,
    orcamento_caminhos: int,
) -> tuple[CaminhoCritico, ...]:
    crit_set = set(tarefas_criticas)
    # Para cada par (u, v) com ligação crítica, escolhe a ligação de menor ID ASCII.
    arestas_por_par: dict[tuple[str, str], DependenciaCalculada] = {}
    for dep in sorted(dependencias_criticas, key=lambda d: d.id):
        par = (dep.origem, dep.destino)
        if par not in arestas_por_par:
            arestas_por_par[par] = dep

    adj: dict[str, list[str]] = {u: [] for u in tarefas_criticas}
    grau_entrada: dict[str, int] = {u: 0 for u in tarefas_criticas}
    for (u, v) in sorted(arestas_por_par):
        adj[u].append(v)
        grau_entrada[v] += 1

    inicios = [u for u in sorted(tarefas_criticas) if grau_entrada[u] == 0]
    finais = {u for u in tarefas_criticas if not adj[u]}

    # Programação dinâmica sobre o DAG crítico para contar caminhos exatos em O(V + E).
    memo_contagem: dict[str, int] = {}

    def contar(u: str) -> int:
        if u in memo_contagem:
            return memo_contagem[u]
        if u in finais:
            memo_contagem[u] = 1
            return 1
        total = sum(contar(v) for v in adj[u])
        memo_contagem[u] = total
        return total

    total_caminhos = sum(contar(u) for u in inicios)
    if total_caminhos > orcamento_caminhos:
        raise CronogramaError(
            "ORCAMENTO_CAMINHOS_EXCEDIDO",
            f"O cronograma possui {total_caminhos} caminhos críticos, acima do orçamento configurado de {orcamento_caminhos}.",
            detalhes={
                "total_caminhos_criticos": total_caminhos,
                "orcamento_caminhos": orcamento_caminhos,
            },
        )

    caminhos: list[CaminhoCritico] = []
    caminho_tarefas: list[str] = []
    caminho_deps: list[str] = []

    def dfs(u: str) -> None:
        caminho_tarefas.append(u)
        if u in finais:
            indice = len(caminhos) + 1
            caminhos.append(
                CaminhoCritico(
                    indice=indice,
                    id=f"CAMINHO-{indice:02d}",
                    tarefas=tuple(caminho_tarefas),
                    dependencias=tuple(caminho_deps),
                    quantidade_tarefas=len(caminho_tarefas),
                    duracao_total_dias=duracao_total_dias,
                )
            )
        else:
            for v in adj[u]:
                caminho_deps.append(arestas_por_par[(u, v)].id)
                dfs(v)
                caminho_deps.pop()
        caminho_tarefas.pop()

    for inicio in inicios:
        dfs(inicio)

    return tuple(caminhos)


def plan_schedule(
    documento: Any,
    *,
    orcamento_caminhos: int | None = None,
) -> ResultadoCronograma:
    """Executa validação topológica, cálculo CPM (FS/SS/FF/SF), calendário e caminhos críticos."""
    fonte_congelada = _freeze(documento)
    projeto = parse_project(documento, orcamento_caminhos_override=orcamento_caminhos)
    ordem_topologica = _ordenacao_topologica_e_ciclos(projeto.tarefas, projeto.dependencias)

    tarefas_por_id: dict[str, TarefaEntrada] = {t.id: t for t in projeto.tarefas}
    deps_ordenadas = tuple(sorted(projeto.dependencias, key=lambda d: d.id))

    entradas_por_tarefa: dict[str, list[DependenciaEntrada]] = {t.id: [] for t in projeto.tarefas}
    saidas_por_tarefa: dict[str, list[DependenciaEntrada]] = {t.id: [] for t in projeto.tarefas}
    for dep in deps_ordenadas:
        entradas_por_tarefa[dep.destino].append(dep)
        saidas_por_tarefa[dep.origem].append(dep)

    # Passo para frente (Forward Pass: ES, EF)
    es: dict[str, int] = {}
    ef: dict[str, int] = {}
    deslocamentos: dict[str, int] = {}
    es_minimos: dict[str, int] = {}

    for t_id in ordem_topologica:
        tarefa = tarefas_por_id[t_id]
        inicio_cedo = 0
        for dep in entradas_por_tarefa[t_id]:
            orig = tarefas_por_id[dep.origem]
            delta = _deslocamento_es(dep.tipo, orig.duracao_dias, tarefa.duracao_dias, dep.defasagem_dias)
            deslocamentos[dep.id] = delta
            req_es = es[dep.origem] + delta
            es_minimos[dep.id] = req_es
            if req_es < 0:
                raise CronogramaError(
                    "DATA_ANTERIOR_AO_INICIO",
                    f"A dependência {dep.id} ({dep.origem} -> {dep.destino}, {dep.tipo}, defasagem {dep.defasagem_dias}d) "
                    f"força início no índice {req_es} (< 0, anterior ao início do projeto).",
                    detalhes={
                        "dependencia_id": dep.id,
                        "origem": dep.origem,
                        "destino": dep.destino,
                        "tipo": dep.tipo,
                        "defasagem_dias": dep.defasagem_dias,
                        "es_calculado": req_es,
                    },
                )
            if req_es > inicio_cedo:
                inicio_cedo = req_es
        es[t_id] = inicio_cedo
        ef[t_id] = inicio_cedo + tarefa.duracao_dias

    duracao_total_dias = max(ef.values())

    # Passo para trás (Backward Pass: LS, LF)
    ls: dict[str, int] = {}
    lf: dict[str, int] = {}
    for t_id in reversed(ordem_topologica):
        tarefa = tarefas_por_id[t_id]
        inicio_tarde = duracao_total_dias - tarefa.duracao_dias
        for dep in saidas_por_tarefa[t_id]:
            candidato = ls[dep.destino] - deslocamentos[dep.id]
            if candidato < inicio_tarde:
                inicio_tarde = candidato
        ls[t_id] = inicio_tarde
        lf[t_id] = inicio_tarde + tarefa.duracao_dias

    # Folgas de ligação e dependências críticas
    folgas_ligacao: dict[str, int] = {}
    for dep in deps_ordenadas:
        folgas_ligacao[dep.id] = es[dep.destino] - es_minimos[dep.id]

    folga_total: dict[str, int] = {}
    folga_livre: dict[str, int] = {}
    critica_por_tarefa: dict[str, bool] = {}

    for t in projeto.tarefas:
        ft = ls[t.id] - es[t.id]
        folga_total[t.id] = ft
        if saidas_por_tarefa[t.id]:
            fl = min(folgas_ligacao[d.id] for d in saidas_por_tarefa[t.id])
        else:
            fl = duracao_total_dias - ef[t.id]
        folga_livre[t.id] = fl
        critica_por_tarefa[t.id] = ft == 0

    dependencias_calculadas: list[DependenciaCalculada] = []
    for dep in projeto.dependencias:
        fl_dep = folgas_ligacao[dep.id]
        eh_critica = (
            critica_por_tarefa[dep.origem]
            and critica_por_tarefa[dep.destino]
            and fl_dep == 0
        )
        dependencias_calculadas.append(
            DependenciaCalculada(
                id=dep.id,
                origem=dep.origem,
                destino=dep.destino,
                tipo=dep.tipo,
                defasagem_dias=dep.defasagem_dias,
                deslocamento_es_dias=deslocamentos[dep.id],
                es_minimo_destino=es_minimos[dep.id],
                folga_ligacao=fl_dep,
                critica=eh_critica,
            )
        )

    mapa_dias_uteis, feriados_horizonte = _construir_calendario(
        projeto.data_inicio,
        projeto.dias_uteis_semana,
        projeto.feriados,
        max(1, duracao_total_dias),
    )

    def data_civil(ponto: int, eh_fim: bool, marco: bool) -> str:
        if marco:
            idx = max(0, ponto - 1)
        elif eh_fim:
            idx = ponto - 1
        else:
            idx = ponto
        return mapa_dias_uteis[idx].data

    tarefas_calculadas: list[TarefaCalculada] = []
    for t in projeto.tarefas:
        marco = t.duracao_dias == 0
        preds = tuple(sorted({d.origem for d in entradas_por_tarefa[t.id]}))
        sucs = tuple(sorted({d.destino for d in saidas_por_tarefa[t.id]}))
        tarefas_calculadas.append(
            TarefaCalculada(
                id=t.id,
                nome=t.nome,
                setor=t.setor,
                duracao_dias=t.duracao_dias,
                marco=marco,
                es=es[t.id],
                ef=ef[t.id],
                ls=ls[t.id],
                lf=lf[t.id],
                folga_total=folga_total[t.id],
                folga_livre=folga_livre[t.id],
                critica=critica_por_tarefa[t.id],
                data_inicio_cedo=data_civil(es[t.id], eh_fim=False, marco=marco),
                data_fim_cedo=data_civil(ef[t.id], eh_fim=True, marco=marco),
                data_inicio_tarde=data_civil(ls[t.id], eh_fim=False, marco=marco),
                data_fim_tarde=data_civil(lf[t.id], eh_fim=True, marco=marco),
                predecessoras=preds,
                sucessoras=sucs,
            )
        )

    tarefas_criticas = tuple(t.id for t in tarefas_calculadas if t.critica)
    deps_criticas = tuple(d for d in dependencias_calculadas if d.critica)
    caminhos_criticos = _enumerar_caminhos_criticos(
        tarefas_criticas,
        deps_criticas,
        duracao_total_dias,
        projeto.orcamento_caminhos,
    )

    data_inicio_util = mapa_dias_uteis[0].data
    data_fim_planejada = mapa_dias_uteis[max(0, duracao_total_dias - 1)].data

    return ResultadoCronograma(
        entrada=projeto,
        data_inicio_util=data_inicio_util,
        data_fim_planejada=data_fim_planejada,
        duracao_total_dias=duracao_total_dias,
        orcamento_caminhos=projeto.orcamento_caminhos,
        feriados_no_horizonte=feriados_horizonte,
        mapa_dias_uteis=mapa_dias_uteis,
        ordem_topologica=ordem_topologica,
        tarefas_criticas=tarefas_criticas,
        tarefas=tuple(tarefas_calculadas),
        dependencias=tuple(dependencias_calculadas),
        caminhos_criticos=caminhos_criticos,
        _fonte_original=fonte_congelada,
    )


def plan_json(
    dados: bytes | str,
    *,
    orcamento_caminhos: int | None = None,
) -> ResultadoCronograma:
    """Decodifica documento UTF-8/JSON estrito e calcula o cronograma crítico."""
    return plan_schedule(loads(dados), orcamento_caminhos=orcamento_caminhos)
