# API Python (`cronograma_critico`)

A biblioteca pública expõe funções puras e estruturas de dados imutáveis (`frozen=True`) para integração direta em aplicações Python 3.11+.

## Funções principais

```python
from pathlib import Path
from cronograma_critico import (
    CronogramaError,
    build_bundle,
    loads,
    parse_project,
    plan_and_write,
    plan_json,
    plan_schedule,
    write_bundle,
)
```

### `plan_json(dados: bytes | str, *, orcamento_caminhos: int | None = None) -> ResultadoCronograma`

Decodifica um documento UTF-8/JSON com o parser estrito (`loads`) e executa o cálculo completo do Método do Caminho Crítico.

```python
entrada_bytes = Path("entrada.json").read_bytes()
resultado = plan_json(entrada_bytes)

print(resultado.duracao_total_dias)      # ex.: 18
print(resultado.data_inicio_util)        # ex.: "2026-03-02"
print(resultado.data_fim_planejada)      # ex.: "2026-03-27"
print(resultado.tarefas_criticas)        # tupla de IDs críticos
for caminho in resultado.caminhos_criticos:
    print(caminho.id, caminho.tarefas, caminho.dependencias)
```

### `plan_schedule(documento: Mapping[str, Any], *, orcamento_caminhos: int | None = None) -> ResultadoCronograma`

Calcula o cronograma a partir de um dicionário já carregado em memória. Congela internamente a estrutura recebida para garantir imutabilidade.

### `build_bundle(resultado: ResultadoCronograma, entrada_bytes: bytes) -> bytes`

Gera em memória os bytes do arquivo `.zip` determinístico com os oito membros (`entrada.json`, `cronograma.json`, `tarefas.csv`, `caminhos-criticos.csv`, `RELATORIO.md`, `gantt-cpm.svg`, `LEIA-ME.md` e `SHA256SUMS`) e executa a autoverificação de integridade SHA-256 e CRC antes de retornar.

### `write_bundle(caminho_saida: str | Path, resultado: ResultadoCronograma, entrada_bytes: bytes) -> Path`

Grava o pacote `.zip` em um arquivo temporário irmão no mesmo diretório e publica o destino por `os.link` exclusivo, recusando sobrescrever qualquer arquivo ou link simbólico preexistente (`DESTINO_EXISTENTE`).

### `plan_and_write(entrada_bytes: bytes, caminho_saida: str | Path, *, orcamento_caminhos: int | None = None) -> ResultadoCronograma`

Atalho que combina `plan_json` e `write_bundle`.

## Tratamento de erros (`CronogramaError`)

Qualquer violação léxica, estrutural, topológica ou de domínio levanta `CronogramaError`:

```python
try:
    plan_json(dados_invalidos)
except CronogramaError as erro:
    print(erro.code)            # ex.: "CICLO_DETECTADO" ou "DATA_ANTERIOR_AO_INICIO"
    print(erro.message)         # mensagem explicativa em português
    print(erro.ciclo)           # ex.: ("T01", "T02", "T03", "T01") quando há ciclo
    print(erro.dependencia_id)  # ex.: "D04" quando uma dependência viola o início
```
