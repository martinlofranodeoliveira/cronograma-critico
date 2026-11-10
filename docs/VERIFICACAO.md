# Verificação e Auditoria Independente

Este projeto possui duas camadas de verificação automatizada além da suíte de testes de unidade (`tests/`):

1. **Verificação da distribuição (`scripts/check_distribution.py`)**: valida o conteúdo fechado do `wheel` e do `sdist`, metadados `METADATA`/`PKG-INFO`, ausência de dependências externas, integridade criptográfica do `RECORD` e instalação isolada fora da árvore de fontes.
2. **Oráculo independente (`tools/verify_independent.py`)**: valida pacotes `.zip` e o executável `cronograma-critico` **sem importar `cronograma_critico`**, resolvendo o sistema de restrições por relaxamento iterativo de Bellman-Ford e conferindo o calendário civil com `datetime.date`.

## 1. Executar a suíte completa de testes

```sh
python -m unittest discover -s tests -v
```

## 2. Construir e auditar os artefatos de distribuição

```sh
python -m build
python scripts/check_distribution.py --dist dist
python scripts/check_distribution.py --install dist/cronograma_critico-1.0.0-py3-none-any.whl
python scripts/check_distribution.py --install dist/cronograma_critico-1.0.0.tar.gz
```

## 3. Auditar um pacote `.zip` com o verificador independente

```sh
cronograma-critico exemplo --saida local/exemplo-cronograma.zip
python tools/verify_independent.py local/exemplo-cronograma.zip --demo
```

## 4. Executar a bateria de verificação sem rede (`unshare`)

Em sistemas Linux com suporte a *user namespaces*:

```sh
unshare --user --map-root-user --net -- python tools/verify_independent.py \
  --require-network-isolation \
  --cli "$(command -v cronograma-critico)" \
  --report local/verify-isolated.json
```
