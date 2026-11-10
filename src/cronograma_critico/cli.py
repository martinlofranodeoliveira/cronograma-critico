"""Interface de linha de comando local, determinística e sem sobrescrita."""
from __future__ import annotations

import argparse
from importlib import resources
import os
import stat
import sys

from . import __version__
from .core import MAX_ORCAMENTO_CAMINHOS, plan_json
from .errors import CronogramaError
from .jsoncodec import MAX_BYTES
from .reports import write_bundle


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CronogramaError("ARGUMENTO_CLI", "Argumentos inválidos; consulte --help.")


def _orcamento_arg(valor: str) -> int:
    try:
        if len(valor) > 6 or not valor.isascii() or not valor.isdecimal():
            raise ValueError
        numero = int(valor)
        if not (1 <= numero <= MAX_ORCAMENTO_CAMINHOS):
            raise ValueError
        return numero
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"O orçamento de caminhos deve ser inteiro entre 1 e {MAX_ORCAMENTO_CAMINHOS}."
        ) from None


def _construir_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="cronograma-critico",
        description=(
            "Planejamento determinístico por Método do Caminho Crítico (CPM) com relações "
            "FS/SS/FF/SF, folgas, feriados e Gantt SVG."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    comandos = parser.add_subparsers(dest="comando", required=True, parser_class=_Parser)

    cmd_planejar = comandos.add_parser(
        "planejar",
        help="Calcular cronograma crítico a partir de um arquivo JSON de entrada.",
    )
    cmd_planejar.add_argument(
        "entrada",
        help="Arquivo regular UTF-8 JSON com o projeto, calendário, tarefas e dependências.",
    )
    cmd_planejar.add_argument(
        "--saida",
        required=True,
        help="Caminho do novo pacote .zip (nunca sobrescreve destino existente).",
    )
    cmd_planejar.add_argument(
        "--orcamento-caminhos",
        type=_orcamento_arg,
        default=None,
        help=f"Limite máximo de caminhos críticos a enumerar (1 a {MAX_ORCAMENTO_CAMINHOS}).",
    )

    cmd_exemplo = comandos.add_parser(
        "exemplo",
        help="Gerar o pacote completo do cenário sintético autoral de 12 tarefas.",
    )
    cmd_exemplo.add_argument(
        "--saida",
        required=True,
        help="Caminho do novo pacote .zip (nunca sobrescreve destino existente).",
    )
    return parser


def _ler_arquivo_entrada(caminho: str) -> bytes:
    descritor: int | None = None
    try:
        antes = os.lstat(caminho)
        if not stat.S_ISREG(antes.st_mode):
            raise CronogramaError(
                "ARQUIVO_ENTRADA",
                "A entrada deve ser um arquivo regular explícito (links simbólicos são recusados).",
            )
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descritor = os.open(caminho, flags)
        aberto = os.fstat(descritor)
        depois = os.lstat(caminho)
        if (
            not stat.S_ISREG(aberto.st_mode)
            or not stat.S_ISREG(depois.st_mode)
            or (antes.st_dev, antes.st_ino) != (aberto.st_dev, aberto.st_ino)
            or (depois.st_dev, depois.st_ino) != (aberto.st_dev, aberto.st_ino)
        ):
            raise CronogramaError(
                "ARQUIVO_ENTRADA",
                "O arquivo de entrada foi alterado durante a abertura ou não é regular.",
            )
        if aberto.st_size > MAX_BYTES:
            raise CronogramaError("LIMITE_JSON", "O arquivo de entrada excede o limite de 8 MiB.")
        blocos: list[bytes] = []
        total = 0
        while True:
            bloco = os.read(descritor, min(65_536, MAX_BYTES + 1 - total))
            if not bloco:
                break
            total += len(bloco)
            if total > MAX_BYTES:
                raise CronogramaError("LIMITE_JSON", "O arquivo de entrada excede o limite de 8 MiB.")
            blocos.append(bloco)
        return b"".join(blocos)
    except CronogramaError:
        raise
    except (OSError, ValueError):
        raise CronogramaError(
            "LEITURA_ENTRADA",
            "Não foi possível ler o arquivo de entrada especificado.",
        ) from None
    finally:
        if descritor is not None:
            os.close(descritor)


def main(argv: list[str] | None = None) -> int:
    """Executa a CLI; retorna 0 em sucesso e 2 em erro de validação ou publicação."""
    try:
        args = _construir_parser().parse_args(argv)
        if args.comando == "exemplo":
            pacote_dados = resources.files("cronograma_critico").joinpath("data")
            entrada_bytes = pacote_dados.joinpath("exemplo.json").read_bytes()
            orcamento = None
        else:
            entrada_bytes = _ler_arquivo_entrada(args.entrada)
            orcamento = args.orcamento_caminhos
        resultado = plan_json(entrada_bytes, orcamento_caminhos=orcamento)
        write_bundle(args.saida, resultado, entrada_bytes)
        print("Pacote de cronograma crítico criado com sucesso.")
        return 0
    except CronogramaError as erro:
        print(f"[{erro.code}] {erro.message}", file=sys.stderr)
        return 2
    except Exception:
        print("[ERRO_CLI] Não foi possível concluir a operação local.", file=sys.stderr)
        return 2
