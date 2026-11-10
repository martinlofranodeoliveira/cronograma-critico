#!/usr/bin/env python3
"""Verificador independente de pacotes ZIP e da CLI cronograma-critico.

Usa exclusivamente a biblioteca padrão do Python e NUNCA importa cronograma_critico.
Resolve o sistema de restrições de precedência de modo independente via
relaxamento iterativo de Bellman-Ford, reconstrói o calendário civil com
datetime.date e confere SHA256SUMS, CSVs, Markdown e o XML do Gantt SVG.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

NAMES = (
    "LEIA-ME.md",
    "RELATORIO.md",
    "SHA256SUMS",
    "caminhos-criticos.csv",
    "cronograma.json",
    "entrada.json",
    "gantt-cpm.svg",
    "tarefas.csv",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _reject_float(_token: str) -> Any:
    raise ValueError("Float não permitido no JSON")


def _reject_constant(_token: str) -> Any:
    raise ValueError("Constante NaN/Infinity não permitida no JSON")


def _unique_pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in items:
        require(k not in out, f"Chave duplicada no JSON: {k}")
        out[k] = v
    return out


def load_strict_json(raw: bytes) -> Any:
    require(not raw.startswith(b"\xef\xbb\xbf"), "BOM UTF-8 encontrado")
    text = raw.decode("utf-8", "strict")
    return json.loads(
        text,
        parse_float=_reject_float,
        parse_constant=_reject_constant,
        object_pairs_hook=_unique_pairs,
    )


def _delta_es(tipo: str, dur_u: int, dur_v: int, lag: int) -> int:
    if tipo == "FS":
        return dur_u + lag
    if tipo == "SS":
        return lag
    if tipo == "FF":
        return dur_u + lag - dur_v
    if tipo == "SF":
        return lag - dur_v
    raise ValueError(f"Tipo de dependência desconhecido: {tipo}")


def _solve_bellman_ford(entrada: dict[str, Any]) -> dict[str, Any]:
    """Resolve ES/EF/LS/LF e caminhos críticos por Bellman-Ford independente."""
    tarefas = entrada["tarefas"]
    dependencias = entrada["dependencias"]
    n = len(tarefas)
    require(n >= 1, "Lista de tarefas vazia")

    dur: dict[str, int] = {}
    for t in tarefas:
        t_id = t["id"]
        require(t_id not in dur, f"Tarefa duplicada: {t_id}")
        require(type(t["duracao_dias"]) is int and t["duracao_dias"] >= 0, "Duração inválida")
        dur[t_id] = t["duracao_dias"]

    edges: list[dict[str, Any]] = []
    for d in dependencias:
        u = d["origem"]
        v = d["destino"]
        tipo = d["tipo"]
        lag = d["defasagem_dias"]
        require(u in dur and v in dur, "Dependência aponta para tarefa inexistente")
        require(u != v, "Auto-laço detectado")
        delta = _delta_es(tipo, dur[u], dur[v], lag)
        edges.append({"id": d["id"], "u": u, "v": v, "tipo": tipo, "lag": lag, "delta": delta})

    # Relaxamento iterativo de Bellman-Ford para ES (caminho mais longo em DAG)
    es: dict[str, int] = {t_id: 0 for t_id in dur}
    for passo in range(n + 1):
        alterou = False
        for e in edges:
            cand = es[e["u"]] + e["delta"]
            if cand > es[e["v"]]:
                require(passo < n, "Ciclo positivo/dirigido detectado por Bellman-Ford")
                es[e["v"]] = cand
                alterou = True
        if not alterou:
            break

    # Confere que nenhuma restrição individual apontou para índice < 0
    for e in edges:
        req = es[e["u"]] + e["delta"]
        require(req >= 0, f"Dependência {e['id']} gera início negativo {req}")
        require(es[e["v"]] >= req, f"Desigualdade violada na dependência {e['id']}")

    ef = {t_id: es[t_id] + dur[t_id] for t_id in dur}
    duracao_total = max(ef.values())

    # Relaxamento iterativo de Bellman-Ford reverso para LS
    ls: dict[str, int] = {t_id: duracao_total - dur[t_id] for t_id in dur}
    for passo in range(n + 1):
        alterou = False
        for e in edges:
            cand = ls[e["v"]] - e["delta"]
            if cand < ls[e["u"]]:
                require(passo < n, "Ciclo detectado no passe reverso")
                ls[e["u"]] = cand
                alterou = True
        if not alterou:
            break

    lf = {t_id: ls[t_id] + dur[t_id] for t_id in dur}
    folga_total = {t_id: ls[t_id] - es[t_id] for t_id in dur}
    for t_id in dur:
        require(folga_total[t_id] == lf[t_id] - ef[t_id], "FT inconsistente entre início e fim")
        require(folga_total[t_id] >= 0, "Folga total negativa")

    folga_ligacao: dict[str, int] = {}
    out_edges: dict[str, list[dict[str, Any]]] = {t_id: [] for t_id in dur}
    for e in edges:
        fl = es[e["v"]] - (es[e["u"]] + e["delta"])
        require(fl >= 0, "Folga de ligação negativa")
        folga_ligacao[e["id"]] = fl
        out_edges[e["u"]].append(e)

    folga_livre: dict[str, int] = {}
    for t_id in dur:
        if out_edges[t_id]:
            folga_livre[t_id] = min(folga_ligacao[e["id"]] for e in out_edges[t_id])
        else:
            folga_livre[t_id] = duracao_total - ef[t_id]
        require(0 <= folga_livre[t_id] <= folga_total[t_id], "Folga livre fora de [0, FT]")

    # Subgrafo crítico independente
    crit_nodes = {t_id for t_id, ft in folga_total.items() if ft == 0}
    crit_pairs: dict[tuple[str, str], str] = {}
    for e in sorted(edges, key=lambda item: item["id"]):
        if e["u"] in crit_nodes and e["v"] in crit_nodes and folga_ligacao[e["id"]] == 0:
            par = (e["u"], e["v"])
            if par not in crit_pairs:
                crit_pairs[par] = e["id"]

    adj_crit: dict[str, list[str]] = {u: [] for u in crit_nodes}
    in_deg_crit: dict[str, int] = {u: 0 for u in crit_nodes}
    for u, v in sorted(crit_pairs):
        adj_crit[u].append(v)
        in_deg_crit[v] += 1

    starts = [u for u in sorted(crit_nodes) if in_deg_crit[u] == 0]
    sinks = {u for u in crit_nodes if not adj_crit[u]}
    for u in starts:
        require(es[u] == 0, "Início de caminho crítico deve ter ES == 0")
    for u in sinks:
        require(ef[u] == duracao_total, "Fim de caminho crítico deve ter EF == duracao_total")

    paths: list[tuple[list[str], list[str]]] = []

    def walk(u: str, acc_t: list[str], acc_d: list[str]) -> None:
        acc_t.append(u)
        if u in sinks:
            paths.append((list(acc_t), list(acc_d)))
        else:
            for v in adj_crit[u]:
                acc_d.append(crit_pairs[(u, v)])
                walk(v, acc_t, acc_d)
                acc_d.pop()
        acc_t.pop()

    for s in starts:
        walk(s, [], [])

    return {
        "es": es,
        "ef": ef,
        "ls": ls,
        "lf": lf,
        "folga_total": folga_total,
        "folga_livre": folga_livre,
        "folga_ligacao": folga_ligacao,
        "duracao_total": duracao_total,
        "crit_nodes": crit_nodes,
        "paths": paths,
    }


def _check_calendar(entrada: dict[str, Any], cronograma: dict[str, Any], duracao_total: int) -> None:
    dias_uteis = set(entrada["calendario"]["dias_uteis_semana"])
    feriados = set(entrada["calendario"]["feriados"])
    cursor = date.fromisoformat(entrada["projeto"]["data_inicio"])
    esperados: list[dict[str, Any]] = []
    necessarios = max(1, duracao_total)
    while len(esperados) < necessarios:
        iso = cursor.isoformat()
        w = cursor.isoweekday()
        if w in dias_uteis and iso not in feriados:
            esperados.append({"indice": len(esperados), "data": iso, "dia_semana_iso": w})
        cursor += timedelta(days=1)

    require(
        cronograma["calendario"]["mapa_dias_uteis"] == esperados,
        "Mapa de dias úteis diverge da reconstrução civil independente",
    )
    require(
        cronograma["projeto"]["data_inicio_util"] == esperados[0]["data"],
        "data_inicio_util divergente",
    )
    require(
        cronograma["projeto"]["data_fim_planejada"] == esperados[max(0, duracao_total - 1)]["data"],
        "data_fim_planejada divergente",
    )

    for t in cronograma["tarefas"]:
        marco = t["duracao_dias"] == 0
        idx_es = max(0, t["es"] - 1) if marco else t["es"]
        idx_ef = max(0, t["ef"] - 1) if marco else t["ef"] - 1
        idx_ls = max(0, t["ls"] - 1) if marco else t["ls"]
        idx_lf = max(0, t["lf"] - 1) if marco else t["lf"] - 1
        require(t["data_inicio_cedo"] == esperados[idx_es]["data"], f"data_inicio_cedo divergente em {t['id']}")
        require(t["data_fim_cedo"] == esperados[idx_ef]["data"], f"data_fim_cedo divergente em {t['id']}")
        require(t["data_inicio_tarde"] == esperados[idx_ls]["data"], f"data_inicio_tarde divergente em {t['id']}")
        require(t["data_fim_tarde"] == esperados[idx_lf]["data"], f"data_fim_tarde divergente em {t['id']}")


def _safe_csv(val: str) -> str:
    if val.startswith(("\t", "\r", "\n")) or val.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + val
    return val


def _check_csvs(files: dict[str, bytes], cronograma: dict[str, Any]) -> tuple[int, int]:
    tarefas_rows = list(csv.reader(io.StringIO(files["tarefas.csv"].decode("utf-8", "strict"))))
    require(
        tarefas_rows[0]
        == [
            "id",
            "nome",
            "setor",
            "duracao_dias",
            "marco",
            "es",
            "ef",
            "ls",
            "lf",
            "folga_total",
            "folga_livre",
            "critica",
            "data_inicio_cedo",
            "data_fim_cedo",
            "data_inicio_tarde",
            "data_fim_tarde",
        ],
        "Cabeçalho de tarefas.csv divergente",
    )
    require(len(tarefas_rows) - 1 == len(cronograma["tarefas"]), "Quantidade de linhas em tarefas.csv divergente")
    for row, t in zip(tarefas_rows[1:], cronograma["tarefas"]):
        expected = [
            _safe_csv(t["id"]),
            _safe_csv(t["nome"]),
            _safe_csv(t["setor"]),
            str(t["duracao_dias"]),
            "sim" if t["marco"] else "nao",
            str(t["es"]),
            str(t["ef"]),
            str(t["ls"]),
            str(t["lf"]),
            str(t["folga_total"]),
            str(t["folga_livre"]),
            "sim" if t["critica"] else "nao",
            t["data_inicio_cedo"],
            t["data_fim_cedo"],
            t["data_inicio_tarde"],
            t["data_fim_tarde"],
        ]
        require(row == expected, f"Linha de tarefas.csv divergente para {t['id']}")

    caminhos_rows = list(csv.reader(io.StringIO(files["caminhos-criticos.csv"].decode("utf-8", "strict"))))
    require(
        caminhos_rows[0]
        == ["indice", "id", "quantidade_tarefas", "duracao_total_dias", "tarefas", "dependencias"],
        "Cabeçalho de caminhos-criticos.csv divergente",
    )
    require(
        len(caminhos_rows) - 1 == len(cronograma["caminhos_criticos"]),
        "Quantidade de linhas em caminhos-criticos.csv divergente",
    )
    for row, c in zip(caminhos_rows[1:], cronograma["caminhos_criticos"]):
        expected = [
            str(c["indice"]),
            _safe_csv(c["id"]),
            str(c["quantidade_tarefas"]),
            str(c["duracao_total_dias"]),
            _safe_csv(" -> ".join(c["tarefas"])),
            _safe_csv(" -> ".join(c["dependencias"])),
        ]
        require(row == expected, f"Linha de caminhos-criticos.csv divergente para {c['id']}")

    return len(tarefas_rows) - 1, len(caminhos_rows) - 1


def _check_svg(svg_bytes: bytes, cronograma: dict[str, Any]) -> None:
    root = ET.fromstring(svg_bytes.decode("utf-8", "strict"))
    ns = {"s": "http://www.w3.org/2000/svg"}
    task_nodes = root.findall(".//s:g[@class='task-row']", ns)
    expected_tasks = [t["id"] for t in cronograma["tarefas"][:40]]
    actual_tasks = [node.get("data-task-id") for node in task_nodes]
    require(actual_tasks == expected_tasks, "Tarefas renderizadas no SVG divergem do esperado")
    visible_set = set(expected_tasks)
    dep_nodes = root.findall(".//s:path[@class='dep-edge']", ns)
    expected_deps = [
        d["id"]
        for d in cronograma["dependencias"]
        if d["origem"] in visible_set and d["destino"] in visible_set
    ]
    actual_deps = [node.get("data-dep-id") for node in dep_nodes]
    require(actual_deps == expected_deps, "Setas de dependência no SVG divergem do esperado")


def _check_demo(cronograma: dict[str, Any]) -> None:
    require(cronograma["projeto"]["id"] == "IMPLANTACAO-LINHA-ENVASE-2026", "ID do projeto demo inesperado")
    require(cronograma["duracao_total_dias"] if "duracao_total_dias" in cronograma else cronograma["resumo"]["duracao_total_dias"] == 18, "Duração total do demo deve ser 18 dias úteis")
    resumo = cronograma["resumo"]
    require(resumo["total_tarefas"] == 12, "Demo deve ter 12 tarefas")
    require(resumo["total_marcos"] == 2, "Demo deve ter 2 marcos")
    require(resumo["total_tarefas_criticas"] == 9, "Demo deve ter 9 tarefas críticas")
    require(resumo["total_tarefas_com_folga"] == 3, "Demo deve ter 3 tarefas com folga")
    require(resumo["total_dependencias"] == 15, "Demo deve ter 15 dependências")
    require(resumo["total_caminhos_criticos"] == 2, "Demo deve ter 2 caminhos críticos empatados")
    require(resumo["data_inicio_util"] == "2026-03-02", "Início útil do demo deve ser 2026-03-02")
    require(resumo["data_fim_planejada"] == "2026-03-27", "Fim planejado do demo deve ser 2026-03-27")
    caminhos = [c["tarefas"] for c in cronograma["caminhos_criticos"]]
    require(
        caminhos
        == [
            ["T01", "T02", "T03", "T04", "T10", "T11", "T12"],
            ["T01", "T02", "T05", "T06", "T10", "T11", "T12"],
        ],
        "Caminhos críticos do exemplo autoral divergem do gabarito",
    )
    por_id = {t["id"]: t for t in cronograma["tarefas"]}
    require(por_id["T07"]["folga_total"] == 4 and por_id["T07"]["folga_livre"] == 0, "Folgas de T07 incorretas")
    require(por_id["T08"]["folga_total"] == 4 and por_id["T08"]["folga_livre"] == 4, "Folgas de T08 incorretas")
    require(por_id["T09"]["folga_total"] == 6 and por_id["T09"]["folga_livre"] == 6, "Folgas de T09 incorretas")


def check_bundle(path: str | Path, *, demo: bool = False, entrada_bytes: bytes | None = None) -> dict[str, Any]:
    require("cronograma_critico" not in sys.modules, "Verificador independente não pode importar cronograma_critico")
    bundle_path = Path(path)
    with zipfile.ZipFile(bundle_path) as zf:
        infos = zf.infolist()
        require(tuple(i.filename for i in infos) == NAMES, "Lista ou ordem de arquivos no ZIP inválida")
        for info in infos:
            require(info.date_time == (1980, 1, 1, 0, 0, 0), "Timestamp ZIP não determinístico")
            require(stat.S_IMODE(info.external_attr >> 16) == 0o644, "Permissão ZIP inesperada")
            require(not (info.flag_bits & 1), "ZIP criptografado não suportado")
        require(zf.testzip() is None, "CRC do ZIP inválido")
        files = {name: zf.read(name) for name in NAMES}

    # Confere SHA256SUMS
    sha_lines = files["SHA256SUMS"].decode("utf-8", "strict").splitlines()
    expected_sha_lines = [
        f"{hashlib.sha256(files[name]).hexdigest()}  {name}"
        for name in NAMES
        if name != "SHA256SUMS"
    ]
    require(sha_lines == expected_sha_lines, "SHA256SUMS diverge dos hashes reais dos arquivos")

    if entrada_bytes is not None:
        require(files["entrada.json"] == entrada_bytes, "Bytes de entrada.json diferem da fonte original")

    entrada = load_strict_json(files["entrada.json"])
    cronograma = load_strict_json(files["cronograma.json"])
    oracle = _solve_bellman_ford(entrada)

    require(
        cronograma["resumo"]["duracao_total_dias"] == oracle["duracao_total"],
        "Duração total diverge do oráculo Bellman-Ford",
    )
    for t in cronograma["tarefas"]:
        tid = t["id"]
        require(t["es"] == oracle["es"][tid], f"ES divergente em {tid}")
        require(t["ef"] == oracle["ef"][tid], f"EF divergente em {tid}")
        require(t["ls"] == oracle["ls"][tid], f"LS divergente em {tid}")
        require(t["lf"] == oracle["lf"][tid], f"LF divergente em {tid}")
        require(t["folga_total"] == oracle["folga_total"][tid], f"FT divergente em {tid}")
        require(t["folga_livre"] == oracle["folga_livre"][tid], f"FL divergente em {tid}")
        require(t["critica"] == (tid in oracle["crit_nodes"]), f"Criticidade divergente em {tid}")

    for d in cronograma["dependencias"]:
        did = d["id"]
        require(d["folga_ligacao"] == oracle["folga_ligacao"][did], f"Folga de ligação divergente em {did}")

    actual_paths = [(c["tarefas"], c["dependencias"]) for c in cronograma["caminhos_criticos"]]
    require(actual_paths == oracle["paths"], "Caminhos críticos divergem do oráculo independente")

    _check_calendar(entrada, cronograma, oracle["duracao_total"])
    n_tarefas_csv, n_caminhos_csv = _check_csvs(files, cronograma)
    _check_svg(files["gantt-cpm.svg"], cronograma)
    require(bool(files["RELATORIO.md"].decode("utf-8", "strict").strip()), "RELATORIO.md vazio")
    require(bool(files["LEIA-ME.md"].decode("utf-8", "strict").strip()), "LEIA-ME.md vazio")

    if demo:
        _check_demo(cronograma)

    return {
        "ok": True,
        "sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        "resumo": cronograma["resumo"],
        "tarefasCsvLinhas": n_tarefas_csv,
        "caminhosCsvLinhas": n_caminhos_csv,
    }


def _check_network_isolation() -> bool:
    net_dev = Path("/proc/net/dev")
    if net_dev.is_file():
        ifaces = []
        for line in net_dev.read_text(encoding="utf-8").splitlines()[2:]:
            if ":" in line:
                ifaces.append(line.split(":", 1)[0].strip())
        if ifaces == ["lo"]:
            return True
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("192.0.2.1", 53))
            return False
        except OSError:
            return True
        finally:
            sock.close()
    except OSError:
        return True


def run_cli_suite(cli_path: str, *, require_network_isolation: bool = False) -> dict[str, Any]:
    require("cronograma_critico" not in sys.modules, "Proibido importar cronograma_critico")
    if require_network_isolation:
        require(_check_network_isolation(), "Isolamento de rede obrigatório não está ativo")

    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update(PYTHONUTF8="1", PYTHONNOUSERSITE="1")

    with tempfile.TemporaryDirectory(prefix="cc-verify-") as tmpdir:
        tmp = Path(tmpdir)
        # 1. Exemplo autoral
        demo_zip = tmp / "exemplo.zip"
        proc = subprocess.run([cli_path, "exemplo", "--saida", str(demo_zip)], capture_output=True, text=True, env=env)
        require(proc.returncode == 0, f"Falha ao gerar exemplo: {proc.stderr}")
        demo_report = check_bundle(demo_zip, demo=True)

        # 2. Recusa de sobrescrita
        before_bytes = demo_zip.read_bytes()
        overwrite = subprocess.run([cli_path, "exemplo", "--saida", str(demo_zip)], capture_output=True, text=True, env=env)
        require(overwrite.returncode == 2 and demo_zip.read_bytes() == before_bytes, "Sobrescrita não foi recusada")

        # 3. Caso sintético com FS, SS, FF, SF, ano bissexto (2024-02-28 -> 2024-02-29) e feriado
        cenario = {
            "schema_version": 1,
            "projeto": {
                "id": "BISSEXTO-2024",
                "titulo": "Teste Independente FS SS FF SF Bissexto",
                "data_inicio": "2024-02-27",
            },
            "calendario": {
                "dias_uteis_semana": [1, 2, 3, 4, 5],
                "feriados": ["2024-02-28"],
            },
            "tarefas": [
                {"id": "A", "nome": "Base", "duracao_dias": 3, "setor": "S1"},
                {"id": "B", "nome": "Paralela SS", "duracao_dias": 4, "setor": "S1"},
                {"id": "C", "nome": "Sincronizada FF", "duracao_dias": 2, "setor": "S2"},
                {"id": "D", "nome": "Reversa SF", "duracao_dias": 2, "setor": "S2"},
                {"id": "M", "nome": "Marco Final", "duracao_dias": 0, "setor": "S3"},
            ],
            "dependencias": [
                {"id": "L1", "origem": "A", "destino": "B", "tipo": "SS", "defasagem_dias": 1},
                {"id": "L2", "origem": "B", "destino": "C", "tipo": "FF", "defasagem_dias": 1},
                {"id": "L3", "origem": "A", "destino": "D", "tipo": "SF", "defasagem_dias": 5},
                {"id": "L4", "origem": "C", "destino": "M", "tipo": "FS", "defasagem_dias": 0},
                {"id": "L5", "origem": "D", "destino": "M", "tipo": "FS", "defasagem_dias": 0},
            ],
        }
        entrada_path = tmp / "bissexto.json"
        entrada_raw = (json.dumps(cenario, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        entrada_path.write_bytes(entrada_raw)
        saida_bissexto = tmp / "bissexto.zip"
        proc2 = subprocess.run(
            [cli_path, "planejar", str(entrada_path), "--saida", str(saida_bissexto)],
            capture_output=True,
            text=True,
            env=env,
        )
        require(proc2.returncode == 0, f"Falha no cenário bissexto: {proc2.stderr}")
        bissexto_report = check_bundle(saida_bissexto, entrada_bytes=entrada_raw)

        # 4. Recusa de ciclo com testemunha
        ciclo_doc = dict(cenario)
        ciclo_doc["dependencias"] = [
            {"id": "C1", "origem": "A", "destino": "B", "tipo": "FS", "defasagem_dias": 0},
            {"id": "C2", "origem": "B", "destino": "C", "tipo": "FS", "defasagem_dias": 0},
            {"id": "C3", "origem": "C", "destino": "A", "tipo": "FS", "defasagem_dias": 0},
        ]
        ciclo_in = tmp / "ciclo.json"
        ciclo_out = tmp / "ciclo.zip"
        ciclo_in.write_text(json.dumps(ciclo_doc), encoding="utf-8")
        proc_ciclo = subprocess.run(
            [cli_path, "planejar", str(ciclo_in), "--saida", str(ciclo_out)],
            capture_output=True,
            text=True,
            env=env,
        )
        require(
            proc_ciclo.returncode == 2
            and "[CICLO_DETECTADO]" in proc_ciclo.stderr
            and "A -> B -> C -> A" in proc_ciclo.stderr
            and not ciclo_out.exists(),
            "Ciclo não foi recusado com caminho testemunha esperado",
        )

        # 5. Recusa de data anterior ao início (ES < 0)
        neg_doc = dict(cenario)
        neg_doc["dependencias"] = [
            {"id": "N1", "origem": "A", "destino": "B", "tipo": "SS", "defasagem_dias": -2},
        ]
        neg_in = tmp / "neg.json"
        neg_out = tmp / "neg.zip"
        neg_in.write_text(json.dumps(neg_doc), encoding="utf-8")
        proc_neg = subprocess.run(
            [cli_path, "planejar", str(neg_in), "--saida", str(neg_out)],
            capture_output=True,
            text=True,
            env=env,
        )
        require(
            proc_neg.returncode == 2
            and "[DATA_ANTERIOR_AO_INICIO]" in proc_neg.stderr
            and "N1" in proc_neg.stderr
            and not neg_out.exists(),
            "Início negativo não foi recusado com código DATA_ANTERIOR_AO_INICIO",
        )

    return {
        "ok": True,
        "networkIsolated": _check_network_isolation(),
        "demo": demo_report,
        "leapYearCase": bissexto_report,
        "refusalsChecked": ["DESTINO_EXISTENTE", "CICLO_DETECTADO", "DATA_ANTERIOR_AO_INICIO"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verificador independente de pacotes cronograma-critico")
    parser.add_argument("bundle", nargs="?", type=Path, help="Pacote .zip a validar")
    parser.add_argument("--demo", action="store_true", help="Confere o gabarito do exemplo de 12 tarefas")
    parser.add_argument("--entrada", type=Path, help="Compara os bytes de entrada.json com este arquivo")
    parser.add_argument("--cli", type=str, help="Executa bateria de verificação independente contra o executável CLI")
    parser.add_argument("--require-network-isolation", action="store_true", help="Exige execução sem rede")
    parser.add_argument("--report", type=Path, help="Salva relatório JSON da verificação")
    args = parser.parse_args(argv)

    try:
        if args.cli:
            result = run_cli_suite(args.cli, require_network_isolation=args.require_network_isolation)
        elif args.bundle:
            raw_in = args.entrada.read_bytes() if args.entrada else None
            result = check_bundle(args.bundle, demo=args.demo, entrada_bytes=raw_in)
        else:
            parser.error("Informe um arquivo .zip ou --cli.")
            return 2
    except Exception as exc:
        print(f"Falha na verificação independente: {exc}", file=sys.stderr)
        return 2

    output = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
