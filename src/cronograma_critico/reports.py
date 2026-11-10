"""Geração determinística do pacote ZIP (JSON, CSV, Markdown, SVG e SHA256SUMS)."""
from __future__ import annotations

import csv
import hashlib
from html import escape
import io
import json
import os
from pathlib import Path
import re
import tempfile
from types import MappingProxyType
from typing import Any
import zipfile

from .core import ResultadoCronograma, plan_json
from .errors import CronogramaError
from .jsoncodec import dumps_output, loads

MAX_BUNDLE_BYTES = 134_217_728

ARQUIVOS_PACOTE = (
    "LEIA-ME.md",
    "RELATORIO.md",
    "SHA256SUMS",
    "caminhos-criticos.csv",
    "cronograma.json",
    "entrada.json",
    "gantt-cpm.svg",
    "tarefas.csv",
)

COLUNAS_TAREFAS_CSV = (
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
)

COLUNAS_CAMINHOS_CSV = (
    "indice",
    "id",
    "quantidade_tarefas",
    "duracao_total_dias",
    "tarefas",
    "dependencias",
)


def _safe_csv_cell(value: Any) -> Any:
    if isinstance(value, str):
        if value.startswith(("\t", "\r", "\n")) or value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
            return "'" + value
    return value


def _md_escape(value: Any) -> str:
    text = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#+.!|~\-])", r"\\\1", text)


def _xml_text(value: str) -> str:
    if any(ord(c) in (0xFFFE, 0xFFFF) for c in value):
        return escape(json.dumps(value, ensure_ascii=True), quote=True)
    return escape(value, quote=True)


def _gerar_tarefas_csv(resultado: ResultadoCronograma) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(COLUNAS_TAREFAS_CSV)
    for t in resultado.tarefas:
        writer.writerow(
            (
                _safe_csv_cell(t.id),
                _safe_csv_cell(t.nome),
                _safe_csv_cell(t.setor),
                t.duracao_dias,
                "sim" if t.marco else "nao",
                t.es,
                t.ef,
                t.ls,
                t.lf,
                t.folga_total,
                t.folga_livre,
                "sim" if t.critica else "nao",
                t.data_inicio_cedo,
                t.data_fim_cedo,
                t.data_inicio_tarde,
                t.data_fim_tarde,
            )
        )
    return stream.getvalue().encode("utf-8")


def _gerar_caminhos_csv(resultado: ResultadoCronograma) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(COLUNAS_CAMINHOS_CSV)
    for c in resultado.caminhos_criticos:
        writer.writerow(
            (
                c.indice,
                _safe_csv_cell(c.id),
                c.quantidade_tarefas,
                c.duracao_total_dias,
                _safe_csv_cell(" -> ".join(c.tarefas)),
                _safe_csv_cell(" -> ".join(c.dependencias)),
            )
        )
    return stream.getvalue().encode("utf-8")


def _gerar_relatorio_md(resultado: ResultadoCronograma) -> bytes:
    proj = resultado.entrada
    total_marcos = sum(1 for t in resultado.tarefas if t.marco)
    total_crit = len(resultado.tarefas_criticas)
    total_folga = len(resultado.tarefas) - total_crit
    total_dep_crit = sum(1 for d in resultado.dependencias if d.critica)
    feriados_txt = ", ".join(resultado.feriados_no_horizonte) if resultado.feriados_no_horizonte else "Nenhum"

    linhas = [
        f"# Relatório de Caminho Crítico — {_md_escape(proj.titulo)}",
        "",
        f"- **ID do Projeto:** `{proj.id}`",
        f"- **Data de Início Declarada:** `{proj.data_inicio}`",
        f"- **Primeiro Dia Útil (`D00`):** `{resultado.data_inicio_util}`",
        f"- **Término Planejado:** `{resultado.data_fim_planejada}`",
        f"- **Duração Total Calculada:** **{resultado.duracao_total_dias} dias úteis**",
        f"- **Dias Úteis da Semana (ISO):** `{', '.join(map(str, proj.dias_uteis_semana))}`",
        f"- **Feriados Descontados no Horizonte:** `{feriados_txt}`",
        "",
        "## 1. Resumo Executivo",
        "",
        "| Indicador | Valor |",
        "| --- | --- |",
        f"| Total de tarefas | {len(resultado.tarefas)} |",
        f"| Marcos (`duracao_dias = 0`) | {total_marcos} |",
        f"| Tarefas críticas (`folga_total = 0`) | {total_crit} |",
        f"| Tarefas com folga (`folga_total > 0`) | {total_folga} |",
        f"| Ligações de precedência | {len(resultado.dependencias)} |",
        f"| Ligações críticas (`folga_ligacao = 0`) | {total_dep_crit} |",
        f"| Caminhos críticos completos enumerados | {len(resultado.caminhos_criticos)} |",
        "",
        "## 2. Caminhos Críticos Determinísticos",
        "",
        "Todos os caminhos críticos empatados entre o início (`ES = 0`) e o término (`EF = duracao_total_dias`) são enumerados em ordem lexicográfica ASCII:",
        "",
        "| # | ID | Qtd. Tarefas | Duração (dias úteis) | Sequência de Tarefas | Ligações Críticas |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for c in resultado.caminhos_criticos:
        seq_t = " → ".join(f"`{t}`" for t in c.tarefas)
        seq_d = ", ".join(f"`{d}`" for d in c.dependencias) if c.dependencias else "—"
        linhas.append(
            f"| {c.indice} | `{c.id}` | {c.quantidade_tarefas} | {c.duracao_total_dias} | {seq_t} | {seq_d} |"
        )

    linhas.extend(
        [
            "",
            "## 3. Quadro de Tarefas (Cedo, Tarde, Folgas e Datas Civis)",
            "",
            "| ID | Tarefa | Setor | Duração | ES..EF | LS..LF | FT | FL | Crítica | Início Cedo | Fim Cedo | Início Tarde | Fim Tarde |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for t in resultado.tarefas:
        crit_txt = "Sim" if t.critica else "Não"
        dur_txt = f"{t.duracao_dias}d (Marco)" if t.marco else f"{t.duracao_dias}d"
        linhas.append(
            f"| `{t.id}` | {_md_escape(t.nome)} | {_md_escape(t.setor)} | {dur_txt} | "
            f"{t.es}..{t.ef} | {t.ls}..{t.lf} | {t.folga_total}d | {t.folga_livre}d | "
            f"{crit_txt} | `{t.data_inicio_cedo}` | `{t.data_fim_cedo}` | `{t.data_inicio_tarde}` | `{t.data_fim_tarde}` |"
        )

    linhas.extend(
        [
            "",
            "## 4. Ligações de Precedência (`FS`, `SS`, `FF`, `SF`)",
            "",
            "| ID | Origem | Destino | Tipo | Defasagem | Deslocamento ES | ES Mín. Destino | Folga de Ligação | Crítica |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for d in resultado.dependencias:
        crit_d = "Sim" if d.critica else "Não"
        linhas.append(
            f"| `{d.id}` | `{d.origem}` | `{d.destino}` | `{d.tipo}` | {d.defasagem_dias:+d}d | "
            f"{d.deslocamento_es_dias:+d}d | {d.es_minimo_destino} | {d.folga_ligacao}d | {crit_d} |"
        )
    if not resultado.dependencias:
        linhas.append("| — | — | — | — | — | — | — | — | — |")

    linhas.extend(
        [
            "",
            "## 5. Calendário de Dias Úteis do Projeto",
            "",
            "| Índice (`D`) | Data Civil (`YYYY-MM-DD`) | Dia da Semana (ISO) |",
            "| --- | --- | --- |",
        ]
    )
    nomes_semana = {
        1: "1 (Segunda-feira)",
        2: "2 (Terça-feira)",
        3: "3 (Quarta-feira)",
        4: "4 (Quinta-feira)",
        5: "5 (Sexta-feira)",
        6: "6 (Sábado)",
        7: "7 (Domingo)",
    }
    for dia in resultado.mapa_dias_uteis[:60]:
        linhas.append(
            f"| `D{dia.indice:02d}` | `{dia.data}` | {nomes_semana[dia.dia_semana_iso]} |"
        )
    if len(resultado.mapa_dias_uteis) > 60:
        linhas.append(
            f"| ... | *(mais {len(resultado.mapa_dias_uteis) - 60} dias úteis em `cronograma.json`)* | ... |"
        )

    linhas.extend(
        [
            "",
            "## 6. Notas de Verificação e Segurança",
            "",
            "- Toda a aritmética de tempos (`ES`, `EF`, `LS`, `LF`, `folga_total`, `folga_livre` e `folga_ligacao`) é executada em inteiros exatos (`int`).",
            "- Para proteger planilhas contra injeção de fórmulas, células textuais em `tarefas.csv` e `caminhos-criticos.csv` iniciadas por `=`, `+`, `-` ou `@` recebem prefixo `'`. Os valores originais permanecem intactos em `cronograma.json`.",
            "- Confira as somas de verificação em `SHA256SUMS` com `sha256sum -c SHA256SUMS`.",
            "",
            "LOFRANO TECNOLOGIA — https://lofranotecnologia.com.br/",
            "",
        ]
    )
    return "\n".join(linhas).encode("utf-8")


def _gerar_svg(resultado: ResultadoCronograma) -> bytes:
    largura = 1440
    tarefas_visiveis = resultado.tarefas[:40]
    n_linhas = len(tarefas_visiveis)
    altura_linha = 42 if n_linhas <= 14 else 34
    topo_grade = 176
    base_grade = topo_grade + max(1, n_linhas) * altura_linha
    altura = max(900, base_grade + 160)

    x_tabela = 28
    largura_tabela = 572
    x_gantt = 616
    largura_gantt = 792
    duracao = max(1, resultado.duracao_total_dias)
    passo_dia = largura_gantt / duracao

    def x_dia(indice_dia: int) -> float:
        clamped = max(0, min(duracao, indice_dia))
        return x_gantt + clamped * passo_dia

    def y_centro(idx_linha: int) -> float:
        return topo_grade + idx_linha * altura_linha + altura_linha / 2.0

    linhas_svg = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{largura}" height="{altura}" '
        f'viewBox="0 0 {largura} {altura}" role="img" aria-labelledby="title desc">',
        f'<title id="title">Cronograma CPM — {_xml_text(resultado.entrada.titulo)}</title>',
        '<desc id="desc">Gráfico de Gantt determinístico do Método do Caminho Crítico (CPM) com barras cedo/tarde, '
        'bigodes de folga total e livre, marcos em losango e setas de precedência FS/SS/FF/SF.</desc>',
        '<defs>',
        '<marker id="arrow-crit" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">',
        '<path d="M 0 1.5 L 8.5 5 L 0 8.5 z" fill="#be123c"/>',
        '</marker>',
        '<marker id="arrow-float" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">',
        '<path d="M 0 2 L 8 5 L 0 8 z" fill="#64748b"/>',
        '</marker>',
        '</defs>',
        f'<rect width="{largura}" height="{altura}" fill="#faf8f5"/>',
        '<g font-family="Inter, Segoe UI, system-ui, -apple-system, sans-serif" fill="#0f172a">',
        # Cabeçalho escuro elegante
        f'<rect x="0" y="0" width="{largura}" height="96" fill="#0f172a"/>',
        '<text x="32" y="38" font-size="22" font-weight="700" fill="#f8fafc">'
        f'{_xml_text(resultado.entrada.titulo)}</text>',
        '<text x="32" y="64" font-size="13" fill="#cbd5e1">'
        f'ID: {_xml_text(resultado.entrada.id)} · Método do Caminho Crítico (CPM FS/SS/FF/SF) · '
        f'Calendário útil: {resultado.data_inicio_util} a {resultado.data_fim_planejada}</text>',
        '<text x="32" y="83" font-size="12" fill="#94a3b8">'
        f'Caminhos críticos empatados: {len(resultado.caminhos_criticos)} · '
        f'Feriados descontados no horizonte: {", ".join(resultado.feriados_no_horizonte) or "nenhum"}</text>',
    ]

    # Cartões de KPI no topo direito
    kpis = [
        ("DURAÇÃO TOTAL", f"{resultado.duracao_total_dias} dias úteis", "#fda4af"),
        ("TAREFAS CRÍTICAS", f"{len(resultado.tarefas_criticas)} de {len(resultado.tarefas)}", "#fda4af"),
        ("CAMINHOS CRÍTICOS", f"{len(resultado.caminhos_criticos)} caminho(s)", "#5eead4"),
        ("LIGAÇÕES TOTAIS", f"{len(resultado.dependencias)} relações", "#cbd5e1"),
    ]
    for idx_kpi, (rotulo, valor, cor_valor) in enumerate(kpis):
        kx = 820 + idx_kpi * 148
        linhas_svg.append(
            f'<rect x="{kx}" y="16" width="138" height="64" rx="6" fill="#1e293b" stroke="#334155"/>'
        )
        linhas_svg.append(
            f'<text x="{kx + 12}" y="38" font-size="10" font-weight="600" fill="#94a3b8">{rotulo}</text>'
        )
        linhas_svg.append(
            f'<text x="{kx + 12}" y="63" font-size="16" font-weight="700" fill="{cor_valor}">{valor}</text>'
        )

    # Faixa de cabeçalho da tabela e da linha do tempo
    linhas_svg.extend(
        [
            f'<rect x="{x_tabela}" y="114" width="{largura_tabela}" height="{base_grade - 114}" rx="6" fill="#ffffff" stroke="#cbd5e1"/>',
            f'<rect x="{x_gantt}" y="114" width="{largura_gantt}" height="{base_grade - 114}" rx="6" fill="#ffffff" stroke="#cbd5e1"/>',
            f'<rect x="{x_tabela}" y="114" width="{largura_tabela}" height="62" rx="6" fill="#f1f5f9" stroke="#cbd5e1"/>',
            f'<rect x="{x_gantt}" y="114" width="{largura_gantt}" height="62" rx="6" fill="#f1f5f9" stroke="#cbd5e1"/>',
            f'<text x="{x_tabela + 12}" y="149" font-size="12" font-weight="700" fill="#334155">ID</text>',
            f'<text x="{x_tabela + 60}" y="149" font-size="12" font-weight="700" fill="#334155">Tarefa</text>',
            f'<text x="{x_tabela + 292}" y="149" font-size="12" font-weight="700" fill="#334155">Setor</text>',
            f'<text x="{x_tabela + 394}" y="149" font-size="12" font-weight="700" fill="#334155">Dur.</text>',
            f'<text x="{x_tabela + 434}" y="149" font-size="12" font-weight="700" fill="#334155">ES..EF</text>',
            f'<text x="{x_tabela + 484}" y="149" font-size="12" font-weight="700" fill="#334155">LS..LF</text>',
            f'<text x="{x_tabela + 532}" y="149" font-size="12" font-weight="700" fill="#334155">FT/FL</text>',
        ]
    )

    # Colunas de dias úteis no painel Gantt
    passo_rotulo = 1 if duracao <= 24 else max(1, duracao // 16)
    mapa = resultado.mapa_dias_uteis
    for d_idx in range(duracao + 1):
        xg = x_dia(d_idx)
        cor_grade = "#cbd5e1" if d_idx in (0, duracao) else "#e2e8f0"
        linhas_svg.append(
            f'<line x1="{xg:.2f}" y1="114" x2="{xg:.2f}" y2="{base_grade}" stroke="{cor_grade}" stroke-width="1"/>'
        )
        if d_idx < duracao:
            # Sombreia colunas alternadas
            if d_idx % 2 == 1:
                linhas_svg.append(
                    f'<rect x="{xg:.2f}" y="{topo_grade}" width="{passo_dia:.2f}" height="{base_grade - topo_grade}" '
                    'fill="#f8fafc" fill-opacity="0.7"/>'
                )
            if d_idx % passo_rotulo == 0:
                cx = xg + passo_dia / 2.0
                linhas_svg.append(
                    f'<text x="{cx:.2f}" y="136" text-anchor="middle" font-size="11" font-weight="700" fill="#1e293b">D{d_idx:02d}</text>'
                )
                if d_idx < len(mapa):
                    partes = mapa[d_idx].data.split("-")
                    dd_mm = f"{partes[2]}/{partes[1]}"
                    linhas_svg.append(
                        f'<text x="{cx:.2f}" y="155" text-anchor="middle" font-size="10" fill="#475569">{dd_mm}</text>'
                    )
            # Marca salto de calendário (fim de semana ou feriado) entre d_idx - 1 e d_idx
            if 0 < d_idx < len(mapa):
                from datetime import date as _date
                anterior = _date.fromisoformat(mapa[d_idx - 1].data)
                atual = _date.fromisoformat(mapa[d_idx].data)
                salto = (atual - anterior).days
                if salto > 1:
                    linhas_svg.append(
                        f'<line x1="{xg:.2f}" y1="{topo_grade}" x2="{xg:.2f}" y2="{base_grade}" '
                        'stroke="#f59e0b" stroke-width="1.6" stroke-dasharray="4,3"/>'
                    )

    # Linhas horizontais e dados das tarefas
    indice_linha_por_id: dict[str, int] = {}
    tarefas_por_id = {t.id: t for t in resultado.tarefas}
    for idx_linha, t in enumerate(tarefas_visiveis):
        indice_linha_por_id[t.id] = idx_linha
        y_top = topo_grade + idx_linha * altura_linha
        yc = y_centro(idx_linha)
        fundo_linha = "#fff1f2" if t.critica else ("#ffffff" if idx_linha % 2 == 0 else "#f8fafc")
        linhas_svg.append(
            f'<rect x="{x_tabela + 1}" y="{y_top}" width="{largura_tabela - 2}" height="{altura_linha}" fill="{fundo_linha}" fill-opacity="0.55"/>'
        )
        linhas_svg.append(
            f'<line x1="{x_tabela}" y1="{y_top + altura_linha}" x2="{x_tabela + largura_tabela}" y2="{y_top + altura_linha}" stroke="#e2e8f0"/>'
        )
        linhas_svg.append(
            f'<line x1="{x_gantt}" y1="{y_top + altura_linha}" x2="{x_gantt + largura_gantt}" y2="{y_top + altura_linha}" stroke="#e2e8f0"/>'
        )

        id_esc = _xml_text(t.id)
        nome_curto = t.nome if len(t.nome) <= 32 else t.nome[:31] + "…"
        setor_curto = t.setor if len(t.setor) <= 13 else t.setor[:12] + "…"
        cor_id = "#be123c" if t.critica else "#0f766e"
        peso_nome = "700" if t.critica else "500"
        dur_label = "0d◆" if t.marco else f"{t.duracao_dias}d"

        linhas_svg.append(
            f'<g class="task-row" data-task-id="{id_esc}" data-critica="{"true" if t.critica else "false"}" '
            f'data-marco="{"true" if t.marco else "false"}">'
            f'<title>{id_esc} — {_xml_text(t.nome)} | ES={t.es} EF={t.ef} LS={t.ls} LF={t.lf} FT={t.folga_total} FL={t.folga_livre}</title>'
        )
        linhas_svg.append(
            f'<text x="{x_tabela + 12}" y="{yc + 4:.2f}" font-size="12" font-weight="700" fill="{cor_id}">{id_esc}</text>'
            f'<text x="{x_tabela + 60}" y="{yc + 4:.2f}" font-size="12" font-weight="{peso_nome}" fill="#0f172a">{_xml_text(nome_curto)}</text>'
            f'<text x="{x_tabela + 292}" y="{yc + 4:.2f}" font-size="11" fill="#475569">{_xml_text(setor_curto)}</text>'
            f'<text x="{x_tabela + 394}" y="{yc + 4:.2f}" font-size="11" font-weight="600" fill="#334155">{dur_label}</text>'
            f'<text x="{x_tabela + 434}" y="{yc + 4:.2f}" font-size="11" fill="#334155">{t.es}..{t.ef}</text>'
            f'<text x="{x_tabela + 484}" y="{yc + 4:.2f}" font-size="11" fill="#475569">{t.ls}..{t.lf}</text>'
            f'<text x="{x_tabela + 532}" y="{yc + 4:.2f}" font-size="11" font-weight="600" fill="{cor_id}">{t.folga_total}/{t.folga_livre}</text>'
        )

        # Desenho da barra ou marco no Gantt
        x_es = x_dia(t.es)
        x_ef = x_dia(t.ef)
        x_ls = x_dia(t.ls)
        x_lf = x_dia(t.lf)

        if t.marco:
            if t.folga_total > 0:
                linhas_svg.append(
                    f'<line x1="{x_es:.2f}" y1="{yc:.2f}" x2="{x_lf:.2f}" y2="{yc:.2f}" '
                    'stroke="#64748b" stroke-width="1.6" stroke-dasharray="4,3"/>'
                )
                linhas_svg.append(
                    f'<polygon points="{x_lf:.2f},{yc - 6:.2f} {x_lf + 6:.2f},{yc:.2f} {x_lf:.2f},{yc + 6:.2f} {x_lf - 6:.2f},{yc:.2f}" '
                    'fill="#e2e8f0" stroke="#64748b" stroke-width="1.2"/>'
                )
            cor_marco = "#e11d48" if t.critica else "#0f766e"
            borda_marco = "#881337" if t.critica else "#115e59"
            linhas_svg.append(
                f'<polygon points="{x_es:.2f},{yc - 8:.2f} {x_es + 8:.2f},{yc:.2f} {x_es:.2f},{yc + 8:.2f} {x_es - 8:.2f},{yc:.2f}" '
                f'fill="{cor_marco}" stroke="{borda_marco}" stroke-width="1.6"/>'
            )
        else:
            altura_barra = min(20.0, altura_linha * 0.50)
            y_barra = yc - altura_barra / 2.0
            largura_cedo = max(4.0, x_ef - x_es)
            # Se tiver folga total, desenha janela tarde tracejada e bigode de folga
            if t.folga_total > 0:
                largura_tarde = max(4.0, x_lf - x_ls)
                linhas_svg.append(
                    f'<rect x="{x_ls:.2f}" y="{y_barra + 3:.2f}" width="{largura_tarde:.2f}" height="{altura_barra - 6:.2f}" '
                    'rx="3" fill="#cbd5e1" fill-opacity="0.55" stroke="#64748b" stroke-width="1" stroke-dasharray="3,2"/>'
                )
                linhas_svg.append(
                    f'<line x1="{x_ef:.2f}" y1="{yc:.2f}" x2="{x_lf:.2f}" y2="{yc:.2f}" '
                    'stroke="#475569" stroke-width="1.8" stroke-dasharray="3,2"/>'
                )
                linhas_svg.append(
                    f'<line x1="{x_lf:.2f}" y1="{yc - 6:.2f}" x2="{x_lf:.2f}" y2="{yc + 6:.2f}" '
                    'stroke="#334155" stroke-width="2"/>'
                )
                if 0 < t.folga_livre < t.folga_total:
                    x_fl = x_dia(t.ef + t.folga_livre)
                    linhas_svg.append(
                        f'<line x1="{x_fl:.2f}" y1="{yc - 5:.2f}" x2="{x_fl:.2f}" y2="{yc + 5:.2f}" '
                        'stroke="#0f766e" stroke-width="2"/>'
                    )
            cor_barra = "#e11d48" if t.critica else "#0f766e"
            borda_barra = "#9f1239" if t.critica else "#115e59"
            linhas_svg.append(
                f'<rect x="{x_es:.2f}" y="{y_barra:.2f}" width="{largura_cedo:.2f}" height="{altura_barra:.2f}" '
                f'rx="4" fill="{cor_barra}" stroke="{borda_barra}" stroke-width="1.3"/>'
            )
            if largura_cedo >= 34:
                linhas_svg.append(
                    f'<text x="{x_es + 6:.2f}" y="{yc + 4:.2f}" font-size="10" font-weight="700" fill="#ffffff">{id_esc}</text>'
                )
        linhas_svg.append("</g>")

    # Setas de precedência entre tarefas visíveis
    for dep in resultado.dependencias:
        if dep.origem not in indice_linha_por_id or dep.destino not in indice_linha_por_id:
            continue
        t_orig = tarefas_por_id[dep.origem]
        t_dest = tarefas_por_id[dep.destino]
        y1 = y_centro(indice_linha_por_id[dep.origem])
        y2 = y_centro(indice_linha_por_id[dep.destino])
        x1 = x_dia(t_orig.es if dep.tipo in ("SS", "SF") else t_orig.ef)
        x2 = x_dia(t_dest.es if dep.tipo in ("FS", "SS") else t_dest.ef)
        sinal_y = 1.0 if y2 >= y1 else -1.0
        meio_y = y1 + sinal_y * (altura_linha * 0.44)
        recuo = 8.0 if dep.tipo in ("FS", "SS") else -8.0
        x_canto = x1 + (6.0 if dep.tipo in ("FS", "FF") else -6.0)
        caminho = (
            f"M {x1:.2f},{y1:.2f} "
            f"L {x_canto:.2f},{y1:.2f} "
            f"L {x_canto:.2f},{meio_y:.2f} "
            f"L {x2 - recuo:.2f},{meio_y:.2f} "
            f"L {x2 - recuo:.2f},{y2:.2f} "
            f"L {x2:.2f},{y2:.2f}"
        )
        cor_seta = "#be123c" if dep.critica else "#64748b"
        largura_seta = "1.7" if dep.critica else "1.2"
        marcador = "url(#arrow-crit)" if dep.critica else "url(#arrow-float)"
        traco = "" if dep.critica else ' stroke-dasharray="3,2"'
        id_dep_esc = _xml_text(dep.id)
        linhas_svg.append(
            f'<path class="dep-edge" data-dep-id="{id_dep_esc}" data-tipo="{dep.tipo}" '
            f'data-critica="{"true" if dep.critica else "false"}" d="{caminho}" '
            f'fill="none" stroke="{cor_seta}" stroke-width="{largura_seta}"{traco} marker-end="{marcador}">'
            f'<title>{id_dep_esc}: {_xml_text(dep.origem)} -> {_xml_text(dep.destino)} ({dep.tipo}, defasagem {dep.defasagem_dias:+d}d, folga {dep.folga_ligacao}d)</title>'
            '</path>'
        )

    # Rodapé e Legenda
    y_leg = base_grade + 34
    linhas_svg.extend(
        [
            f'<rect x="28" y="{y_leg - 18}" width="{largura - 56}" height="106" rx="6" fill="#ffffff" stroke="#cbd5e1"/>',
            f'<text x="46" y="{y_leg + 6}" font-size="12" font-weight="700" fill="#1e293b">Legenda Analítica CPM:</text>',
            # Item 1: Crítica
            f'<rect x="210" y="{y_leg - 6}" width="24" height="14" rx="3" fill="#e11d48" stroke="#9f1239"/>',
            f'<text x="242" y="{y_leg + 6}" font-size="12" fill="#334155">Tarefa crítica (folga_total = 0)</text>',
            # Item 2: Não crítica
            f'<rect x="460" y="{y_leg - 6}" width="24" height="14" rx="3" fill="#0f766e" stroke="#115e59"/>',
            f'<text x="492" y="{y_leg + 6}" font-size="12" fill="#334155">Tarefa com folga (ES..EF)</text>',
            # Item 3: Janela tarde / folga
            f'<rect x="685" y="{y_leg - 5}" width="24" height="12" rx="2" fill="#cbd5e1" stroke="#64748b" stroke-dasharray="3,2"/>',
            f'<text x="717" y="{y_leg + 6}" font-size="12" fill="#334155">Janela tarde (LS..LF) e bigode de folga total</text>',
            # Item 4: Marco
            f'<polygon points="1030,{y_leg - 7} 1037,{y_leg + 1} 1030,{y_leg + 9} 1023,{y_leg + 1}" fill="#e11d48" stroke="#881337"/>',
            f'<text x="1046" y="{y_leg + 6}" font-size="12" fill="#334155">Marco (0 dias)</text>',
            # Item 5: Salto calendário
            f'<line x1="1175" y1="{y_leg - 7}" x2="1175" y2="{y_leg + 9}" stroke="#f59e0b" stroke-width="2" stroke-dasharray="4,3"/>',
            f'<text x="1185" y="{y_leg + 6}" font-size="12" fill="#334155">Salto fim de semana / feriado</text>',
        ]
    )

    caminhos_resumo = "   |   ".join(
        f"{c.id}: {' → '.join(c.tarefas)} ({c.duracao_total_dias}d)"
        for c in resultado.caminhos_criticos[:3]
    )
    if len(resultado.caminhos_criticos) > 3:
        caminhos_resumo += f"   (+{len(resultado.caminhos_criticos) - 3} caminhos em cronograma.json)"

    linhas_svg.extend(
        [
            f'<text x="46" y="{y_leg + 36}" font-size="12" font-weight="600" fill="#be123c">'
            f'Caminhos críticos ({len(resultado.caminhos_criticos)}): {_xml_text(caminhos_resumo)}</text>',
            f'<text x="46" y="{y_leg + 62}" font-size="11" fill="#64748b">'
            f'Rótulos visíveis: {len(tarefas_visiveis)} de {len(resultado.tarefas)} tarefas. '
            'Cálculo inteiramente determinístico e local em inteiros exatos — LOFRANO TECNOLOGIA.</text>',
            '</g>',
            '</svg>',
            '',
        ]
    )
    return "\n".join(linhas_svg).encode("utf-8")


_LEIA_ME = """# Pacote de Cronograma Crítico (CPM)

Este arquivo ZIP foi gerado localmente pelo pacote `cronograma-critico`
(LOFRANO TECNOLOGIA), sem uso de rede, serviços externos ou aritmética de ponto
flutuante.

## Arquivos incluídos

- `entrada.json` — bytes exatos da especificação de entrada recebida.
- `cronograma.json` — memória de cálculo estruturada completa: datas civis,
  índices úteis (`ES`, `EF`, `LS`, `LF`), `folga_total`, `folga_livre`,
  `folga_ligacao`, mapa de calendário e todos os caminhos críticos enumerados.
- `tarefas.csv` — tabela de tarefas com proteção contra injeção de fórmulas em
  planilhas (textos iniciados por `=`, `+`, `-` ou `@` recebem apóstrofo `'`).
- `caminhos-criticos.csv` — todos os caminhos críticos empatados em ordem ASCII.
- `RELATORIO.md` — relatório de auditoria legível em português.
- `gantt-cpm.svg` — diagrama de Gantt vetorial com barras cedo/tarde, bigodes de
  folga, marcos em losango e setas de precedência (`FS`, `SS`, `FF`, `SF`).
- `LEIA-ME.md` — este guia de leitura do pacote.
- `SHA256SUMS` — somas de verificação SHA-256 dos demais sete arquivos.

## Conferência de integridade

Para validar os arquivos extraídos em sistemas POSIX:

```sh
sha256sum -c SHA256SUMS
```

LOFRANO TECNOLOGIA — https://lofranotecnologia.com.br/
"""


def _mesmo_json(left: Any, right: Any) -> bool:
    if isinstance(left, MappingProxyType):
        left = dict(left)
    if isinstance(right, MappingProxyType):
        right = dict(right)
    if isinstance(left, tuple):
        left = list(left)
    if isinstance(right, tuple):
        right = list(right)
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_mesmo_json(left[k], right[k]) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(_mesmo_json(a, b) for a, b in zip(left, right))
    return left == right


def build_bundle(
    resultado: ResultadoCronograma,
    entrada_bytes: bytes,
) -> bytes:
    """Constrói e verifica em memória o arquivo ZIP determinístico de 8 membros."""
    if type(entrada_bytes) is not bytes:
        raise CronogramaError("FONTE_RELATORIO", "Os bytes da entrada original devem ser fornecidos.")
    if not _mesmo_json(loads(entrada_bytes), resultado.source_document()):
        raise CronogramaError("FONTE_RELATORIO", "Os bytes da entrada não correspondem ao cronograma calculado.")

    arquivos: dict[str, bytes] = {
        "entrada.json": entrada_bytes,
        "cronograma.json": dumps_output(resultado.to_dict()).encode("utf-8"),
        "tarefas.csv": _gerar_tarefas_csv(resultado),
        "caminhos-criticos.csv": _gerar_caminhos_csv(resultado),
        "RELATORIO.md": _gerar_relatorio_md(resultado),
        "gantt-cpm.svg": _gerar_svg(resultado),
        "LEIA-ME.md": _LEIA_ME.encode("utf-8"),
    }
    linhas_sha = [
        f"{hashlib.sha256(arquivos[nome]).hexdigest()}  {nome}\n"
        for nome in sorted(arquivos)
    ]
    arquivos["SHA256SUMS"] = "".join(linhas_sha).encode("utf-8")

    if sum(len(conteudo) for conteudo in arquivos.values()) > MAX_BUNDLE_BYTES:
        raise CronogramaError("LIMITE_RELATORIO", "O pacote excede o limite de tamanho descompactado.")

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for nome in sorted(arquivos):
            info = zipfile.ZipInfo(nome, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, arquivos[nome], compress_type=zipfile.ZIP_DEFLATED)

    pacote_bytes = stream.getvalue()
    _verificar_pacote_memoria(pacote_bytes, arquivos)
    return pacote_bytes


def _verificar_pacote_memoria(pacote_bytes: bytes, esperados: dict[str, bytes]) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(pacote_bytes)) as archive:
            if tuple(archive.namelist()) != ARQUIVOS_PACOTE or archive.testzip() is not None:
                raise ValueError
            for nome, conteudo in esperados.items():
                recebido = archive.read(nome)
                if len(recebido) != len(conteudo) or hashlib.sha256(recebido).digest() != hashlib.sha256(conteudo).digest():
                    raise ValueError
    except (ValueError, OSError, zipfile.BadZipFile):
        raise CronogramaError("RELATORIO_INVALIDO", "Falha na autoverificação do pacote ZIP.") from None


def write_bundle(
    caminho_saida: str | os.PathLike[str],
    resultado: ResultadoCronograma,
    entrada_bytes: bytes,
) -> Path:
    """Publica o ZIP sem sobrescrever destino existente usando arquivo temporário irmão e os.link."""
    conteudo = build_bundle(resultado, entrada_bytes)
    destino = Path(caminho_saida)
    temporario: Path | None = None
    try:
        if destino.suffix.lower() != ".zip":
            raise CronogramaError("DESTINO_INVALIDO", "O caminho de saída deve terminar com extensão .zip.")
        if not destino.parent.is_dir():
            raise CronogramaError("DIRETORIO_SAIDA_INEXISTENTE", "O diretório pai da saída deve existir.")
        if os.path.lexists(destino):
            raise CronogramaError("DESTINO_EXISTENTE", "O arquivo de destino já existe e não será sobrescrito.")
        descritor, nome_temp = tempfile.mkstemp(
            prefix=".cronograma-critico-",
            suffix=".tmp",
            dir=destino.parent,
        )
        temporario = Path(nome_temp)
        with os.fdopen(descritor, "wb") as handle:
            handle.write(conteudo)
            handle.flush()
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), 0o644)
            os.fsync(handle.fileno())
        if temporario.read_bytes() != conteudo:
            raise CronogramaError("RELATORIO_INVALIDO", "Falha na verificação dos bytes preparados.")
        try:
            os.link(temporario, destino)
        except FileExistsError:
            raise CronogramaError("DESTINO_EXISTENTE", "O arquivo de destino já existe e não será sobrescrito.") from None
        except OSError:
            raise CronogramaError(
                "PUBLICACAO_LINK",
                "Não foi possível publicar o pacote por hardlink exclusivo neste sistema de arquivos.",
            ) from None
        return destino
    except CronogramaError:
        raise
    except (OSError, ValueError):
        raise CronogramaError("ERRO_SAIDA", "Não foi possível gravar o pacote no diretório de saída.") from None
    finally:
        if temporario is not None:
            try:
                temporario.unlink()
            except OSError:
                pass


def plan_and_write(
    entrada_bytes: bytes,
    caminho_saida: str | os.PathLike[str],
    *,
    orcamento_caminhos: int | None = None,
) -> ResultadoCronograma:
    """Decodifica a entrada, calcula o cronograma e publica o pacote ZIP."""
    resultado = plan_json(entrada_bytes, orcamento_caminhos=orcamento_caminhos)
    write_bundle(caminho_saida, resultado, entrada_bytes)
    return resultado
