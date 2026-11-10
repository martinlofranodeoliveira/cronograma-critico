"""Erros públicos com códigos estáveis e detalhes estruturados imutáveis."""
from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


class CronogramaError(ValueError):
    """Exceção determinística de validação, cálculo ou publicação."""

    def __init__(
        self,
        code: str,
        message: str,
        path: str = "",
        detalhes: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.path = path
        self.detalhes = _freeze(dict(detalhes or {}))
        super().__init__(message)

    @property
    def ciclo(self) -> tuple[str, ...] | None:
        valor = self.detalhes.get("ciclo")
        return tuple(valor) if valor is not None else None

    @property
    def dependencia_id(self) -> str | None:
        valor = self.detalhes.get("dependencia_id")
        return str(valor) if valor is not None else None
