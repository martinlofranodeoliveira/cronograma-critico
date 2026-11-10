"""Leitor e serializador JSON estrito, limitado e exclusivamente inteiro."""
from __future__ import annotations

import json
import re
from types import MappingProxyType
from typing import Any

from .errors import CronogramaError

MAX_BYTES = 8_388_608
MAX_DEPTH = 16
MAX_NODES = 200_000
MAX_DIGITS = 18
MAX_OUTPUT_BYTES = 67_108_864
MAX_OUTPUT_NODES = 1_000_000
_INT_TOKEN = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")


def _error(message: str, code: str = "JSON_INVALIDO", path: str = "") -> CronogramaError:
    return CronogramaError(code, message, path=path)


def _parse_int(token: str) -> int:
    if _INT_TOKEN.fullmatch(token) is None:
        raise _error("Inteiro JSON inválido.")
    digits = token[1:] if token.startswith("-") else token
    if len(digits) > MAX_DIGITS:
        raise _error("Inteiro excede o limite de dígitos.", "LIMITE_JSON")
    return int(token)


def _reject_float(_token: str) -> Any:
    raise _error(
        "Números com ponto decimal ou expoente não são permitidos; use inteiros exatos.",
        "NUMERO_NAO_INTEIRO",
    )


def _reject_constant(_token: str) -> Any:
    raise _error("Constantes não finitas (NaN/Infinity) não são JSON válido.")


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in key):
            raise _error("Chave JSON contém surrogate Unicode inválido.")
        if key in result:
            raise _error(f"Objeto JSON contém chave duplicada: {key!r}.", "CHAVE_DUPLICADA")
        result[key] = value
    return result


def _string(value: str) -> str:
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
        raise _error("Texto contém surrogate Unicode inválido.")
    return json.dumps(value, ensure_ascii=False)


def _dumps(value: Any, *, max_nodes: int, max_bytes: int) -> str:
    nodes = 0

    def emit(item: Any, depth: int) -> str:
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes:
            raise _error("Documento excede o limite de nós.", "LIMITE_JSON")
        kind = type(item)
        if kind is dict or kind is MappingProxyType or kind is list or kind is tuple:
            if depth > MAX_DEPTH:
                raise _error("Documento excede o limite de profundidade.", "LIMITE_JSON")
            is_mapping = kind is dict or kind is MappingProxyType
            if not item:
                return "{}" if is_mapping else "[]"
            indentation = "  " * depth
            if is_mapping:
                rows = []
                for key, child in item.items():
                    if type(key) is not str:
                        raise _error("Chaves JSON devem ser texto.")
                    rows.append(indentation + _string(key) + ": " + emit(child, depth + 1))
                return "{\n" + ",\n".join(rows) + "\n" + "  " * (depth - 1) + "}"
            rows = [indentation + emit(child, depth + 1) for child in item]
            return "[\n" + ",\n".join(rows) + "\n" + "  " * (depth - 1) + "]"
        if kind is str:
            return _string(item)
        if item is None:
            return "null"
        if kind is bool:
            return "true" if item else "false"
        if kind is int:
            if len(str(abs(item))) > MAX_DIGITS:
                raise _error("Inteiro excede o limite de dígitos.", "LIMITE_JSON")
            return str(item)
        if kind is float:
            raise _error("Floats não são permitidos na serialização JSON.", "NUMERO_NAO_INTEIRO")
        raise _error("Documento contém tipo não suportado.")

    result = emit(value, 1) + "\n"
    if len(result.encode("utf-8")) > max_bytes:
        raise _error("Documento excede o limite de bytes canônicos.", "LIMITE_JSON")
    return result


def dumps(value: Any) -> str:
    """Serializa JSON de entrada com recuo de 2 espaços e LF final."""
    return _dumps(value, max_nodes=MAX_NODES, max_bytes=MAX_BYTES)


def dumps_output(value: Any) -> str:
    """Serializa JSON derivado de saída mantendo proibições de tipo."""
    return _dumps(value, max_nodes=MAX_OUTPUT_NODES, max_bytes=MAX_OUTPUT_BYTES)


def loads(data: bytes | str) -> Any:
    """Decodifica UTF-8 sem BOM; recusa duplicatas, floats, NaN/Infinity e limites."""
    if type(data) is bytes:
        if len(data) > MAX_BYTES:
            raise _error("Documento excede o limite de bytes.", "LIMITE_JSON")
        if data.startswith(b"\xef\xbb\xbf"):
            raise _error("BOM UTF-8 não é permitido.")
        try:
            source = data.decode("utf-8", "strict")
        except UnicodeError:
            raise _error("Documento não é UTF-8 válido.") from None
    elif type(data) is str:
        try:
            encoded = data.encode("utf-8", "strict")
        except UnicodeError:
            raise _error("Texto contém Unicode inválido.") from None
        if len(encoded) > MAX_BYTES:
            raise _error("Documento excede o limite de bytes.", "LIMITE_JSON")
        source = data
    else:
        raise _error("A entrada deve ser bytes ou texto UTF-8.")
    if source.startswith("\ufeff"):
        raise _error("BOM UTF-8 não é permitido.")

    depth = 0
    quoted = escaped = False
    for ch in source:
        if quoted:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
        elif ch in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                raise _error("Documento excede o limite de profundidade.", "LIMITE_JSON")
        elif ch in "]}":
            depth -= 1

    try:
        value = json.loads(
            source,
            parse_int=_parse_int,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
            object_pairs_hook=_pairs,
        )
    except CronogramaError:
        raise
    except (json.JSONDecodeError, RecursionError, OverflowError):
        raise _error("Sintaxe JSON inválida.") from None

    dumps(value)
    return value
