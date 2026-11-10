# Interoperabilidade (CSV, JSON, SVG e Ferramentas de Gestão)

Os artefatos gerados pelo `cronograma-critico` usam formatos abertos e determinísticos para facilitar auditoria em planilhas, bancos relacionais, notebooks de engenharia e navegadores web.

## 1. `cronograma.json`

Documento UTF-8 JSON indentado com 2 espaços, contendo apenas inteiros exatos, booleanos, strings e listas/objetos. Pode ser lido diretamente por `jq`, Python (`json.loads`), R, DuckDB ou PostgreSQL (`jsonb`).

Exemplo de consulta com `jq` para listar as tarefas críticas e suas datas civis:

```sh
jq '.tarefas[] | select(.critica) | {id, nome, es, ef, data_inicio_cedo, data_fim_cedo}' cronograma.json
```

## 2. `tarefas.csv` e `caminhos-criticos.csv`

- Codificados em UTF-8 sem BOM, separador vírgula (`,`) e fim de linha LF (`\n`).
- **Proteção contra injeção de fórmulas (CSV Formula Injection)**: qualquer campo textual cujo conteúdo (após espaços iniciais) comece com `=`, `+`, `-` ou `@`, ou que inicie por tabulação/quebra de linha, recebe um apóstrofo inicial (`'`) no CSV. O texto original sem apóstrofo permanece preservado em `cronograma.json`.

## 3. `gantt-cpm.svg`

Arquivo SVG 1.1 autônomo (`1440×900` por padrão), sem fontes externas obrigatórias nem scripts executáveis:

- Cada linha de tarefa possui `<g class="task-row" data-task-id="..." data-critica="true|false" data-marco="true|false">`.
- Cada seta de precedência possui `<path class="dep-edge" data-dep-id="..." data-tipo="FS|SS|FF|SF" data-critica="true|false">`.
- Pode ser aberto diretamente em qualquer navegador ou convertido para PNG/PDF via `google-chrome --headless --screenshot` ou `rsvg-convert`.

## 4. `SHA256SUMS`

Segue o formato padrão do utilitário GNU `sha256sum` (duas colunas separadas por dois espaços), permitindo validação imediata após descompactação:

```sh
sha256sum -c SHA256SUMS
```
