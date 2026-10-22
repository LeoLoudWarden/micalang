import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .engine import MicaEngine, MicaError


def _read_stdin() -> str | None:
    value = sys.stdin.readline()
    return value if value else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mica", description="MicaJIT-PY prototype")
    parser.add_argument("source", help="Mica source file")
    args = parser.parse_args(argv)
    try:
        source = Path(args.source).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"mica: unable to read source: {exc.strerror or 'I/O error'}", file=sys.stderr)
        return 1

    engine = MicaEngine()
    try:
        root = engine.create_root(source, input_provider=_read_stdin)
    except MicaError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        root.run()
    except MicaError as exc:
        sys.stdout.write(root.stdout())
        print(exc, file=sys.stderr)
        return 1

    sys.stdout.write(root.stdout())
    snapshot = root.snapshot()
    if snapshot["state"] == "waiting_input":
        error = MicaError(
            "input required",
            kind="input_error",
            filename="<mica>",
            line=snapshot["next_line"],
        )
        print(error, file=sys.stderr)
        return 1
    return 0
