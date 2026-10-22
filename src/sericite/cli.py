import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .engine import SericiteEngine, SericiteError


def _read_stdin() -> str | None:
    value = sys.stdin.readline()
    return value if value else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sericite",
        description="Sericite language runtime (Sericite-PY reference implementation)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("source", help="Sericite source file")
    args = parser.parse_args(argv)
    try:
        source = Path(args.source).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"sericite: unable to read source: {exc.strerror or 'I/O error'}", file=sys.stderr)
        return 1

    engine = SericiteEngine()
    try:
        root = engine.create_root(source, input_provider=_read_stdin)
    except SericiteError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        root.run()
    except SericiteError as exc:
        sys.stdout.write(root.stdout())
        print(exc, file=sys.stderr)
        return 1

    sys.stdout.write(root.stdout())
    snapshot = root.snapshot()
    if snapshot["state"] == "waiting_input":
        error = SericiteError(
            "input required",
            kind="input_error",
            filename="<sericite>",
            line=snapshot["next_line"],
        )
        print(error, file=sys.stderr)
        return 1
    return 0
