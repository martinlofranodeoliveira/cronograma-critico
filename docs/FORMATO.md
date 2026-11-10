# Formato de Entrada e Saída (`schema_version: 1`)

Este documento especifica o esquema estrito de entrada (`entrada.json`), as regras de recusa e o formato dos oito arquivos gerados no pacote `.zip`.

## 1. Regras léxicas e estruturais do JSON

- Codificação obrigatória: **UTF-8 sem BOM**.
- Números devem ser **inteiros exatos** (`int`). Qualquer número com ponto decimal (`1.0`, `2.5`), expoente (`1e3`) ou constante não finita (`NaN`, `Infinity`) é recusado com `NUMERO_NAO_INTEIRO` ou `JSON_INVALIDO`.
- Valores booleanos (`true`/`false`) não são aceitos onde inteiros são esperados (`ESQUEMA_INVALIDO`).
- Objetos JSON com chaves duplicadas são recusados com `CHAVE_DUPLICADA`.
- Campos não previstos na especificação são recusados com `CAMPO_DESCONHECIDO`.
- Limites do documento de entrada: até `8 MiB` (`8.388.608` bytes), profundidade máxima `16` e até `200.000` nós JSON.

## 2. Esquema do documento de entrada

```json
{
  "schema_version": 1,
  "projeto": {
    "id": "IMPLANTACAO-LINHA-ENVASE-2026",
    "titulo": "Implantação de Telemetria e CLP da Linha de Envase 04",
    "data_inicio": "2026-03-02"
  },
  "calendario": {
    "dias_uteis_semana": [1, 2, 3, 4, 5],
    "feriados": ["2026-03-09", "2026-03-19"]
  },
  "orcamento_caminhos": 1000,
  "tarefas": [
    {
      "id": "T01",
      "nome": "Marco de Abertura e Bloqueio LOTO",
      "duracao_dias": 0,
      "setor": "Planejamento"
    }
  ],
  "dependencias": []
}
```

### Campos da raiz

| Campo | Tipo | Obrigatório | Limites e regras |
| --- | --- | --- | --- |
| `schema_version` | `int` | Sim | Deve ser exatamente `1` (`VERSAO_ESQUEMA_INVALIDA`). |
| `projeto` | `object` | Sim | `{id, titulo, data_inicio}`. |
| `calendario` | `object` | Sim | `{dias_uteis_semana, feriados}`. |
| `orcamento_caminhos` | `int` | Não | `1..10000` (padrão: `1000`). Máximo de caminhos críticos enumerados. |
| `tarefas` | `list` | Sim | `1..1000` objetos `{id, nome, duracao_dias, setor}`. |
| `dependencias` | `list` | Sim | `0..5000` objetos `{id, origem, destino, tipo, defasagem_dias}`. |

### Regras de identificadores, datas e limites

- `id` (`projeto.id`, `tarefas[i].id`, `dependencias[i].id`): ASCII estrito `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`.
- `data_inicio` e itens de `feriados`: formato ISO `YYYY-MM-DD` válido no calendário gregoriano entre `1970-01-01` e `2099-12-31` (`DATA_INVALIDA`).
- `dias_uteis_semana`: lista de 1 a 7 inteiros distintos entre `1` (segunda-feira) e `7` (domingo) (`CALENDARIO_INVALIDO`).
- `duracao_dias`: inteiro entre `0` e `3650` (`0` representa um marco / *milestone*).
- `tipo`: `"FS"`, `"SS"`, `"FF"` ou `"SF"` (`DEPENDENCIA_INVALIDA`).
- `defasagem_dias`: inteiro entre `-365` e `365` (`DEPENDENCIA_INVALIDA`).

## 3. Mapeamento entre índices úteis e datas civis

Seja $W_0, W_1, W_2, \dots$ a sequência ordenada de datas civis $\ge \text{projeto.data\_inicio}$ cujo dia da semana ISO pertence a `dias_uteis_semana` e que não constam em `feriados`:

- Para tarefas com `duracao_dias > 0`:
  - `data_inicio_cedo = W[es]`
  - `data_fim_cedo = W[ef - 1]`
  - `data_inicio_tarde = W[ls]`
  - `data_fim_tarde = W[lf - 1]`
- Para marcos (`duracao_dias == 0`, onde `ef == es` e `lf == ls`):
  - `data_inicio_cedo = data_fim_cedo = W[max(0, es - 1)]`
  - `data_inicio_tarde = data_fim_tarde = W[max(0, ls - 1)]`

## 4. Códigos de erro públicos

| Código | Condição |
| --- | --- |
| `JSON_INVALIDO` | Sintaxe JSON inválida, UTF-8 inválido, BOM UTF-8 ou constante `NaN`/`Infinity` |
| `LIMITE_JSON` | Tamanho (`> 8 MiB`), profundidade (`> 16`), nós (`> 200.000`) ou dígitos (`> 18`) excedidos |
| `CHAVE_DUPLICADA` | Chave repetida no mesmo objeto JSON |
| `NUMERO_NAO_INTEIRO` | Número com ponto decimal ou notação científica |
| `VERSAO_ESQUEMA_INVALIDA` | `schema_version` diferente de `1` |
| `CAMPO_DESCONHECIDO` | Chave fora da especificação em qualquer objeto |
| `ESQUEMA_INVALIDO` | Campo obrigatório ausente, tipo incorreto ou identificador/texto fora do padrão |
| `DATA_INVALIDA` | Data fora do formato `YYYY-MM-DD` ou inexistente no calendário civil |
| `CALENDARIO_INVALIDO` | `dias_uteis_semana` vazio/fora de `1..7` ou datas repetidas em `feriados` |
| `TAREFA_DUPLICADA` | Duas tarefas com o mesmo `id` |
| `TAREFA_INEXISTENTE` | Dependência referencia `origem` ou `destino` inexistente |
| `DEPENDENCIA_DUPLICADA` | `id` de dependência repetido ou par `(origem, destino, tipo)` duplicado |
| `DEPENDENCIA_INVALIDA` | `tipo` fora de `FS/SS/FF/SF` ou `defasagem_dias` fora de `-365..365` |
| `CICLO_DETECTADO` | Auto-laço ou ciclo dirigido; informa o ciclo testemunha fechado `[t1, ..., t1]` |
| `DATA_ANTERIOR_AO_INICIO` | Defasagem negativa força `ES < 0` em alguma dependência |
| `ORCAMENTO_CAMINHOS_EXCEDIDO` | Quantidade de caminhos críticos excede `orcamento_caminhos` |
| `ARQUIVO_ENTRADA` | Caminho de entrada não é arquivo regular (ex.: link simbólico ou diretório) |
| `DESTINO_EXISTENTE` | Caminho `--saida` já existe e é preservado sem sobrescrita |
