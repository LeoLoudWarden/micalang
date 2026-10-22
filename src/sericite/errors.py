from __future__ import annotations


class SericiteError(Exception):
    def __init__(
        self,
        message: str,
        *,
        kind: str = "runtime_error",
        filename: str | None = None,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.filename = filename
        self.line = line
        self.column = column

    def bind(self, filename: str, line: int | None) -> SericiteError:
        if self.filename is None:
            self.filename = filename
        if self.line is None:
            self.line = line
        return self

    def __str__(self) -> str:
        location = self.filename or "<sericite>"
        if self.line is not None:
            location += f":{self.line}"
            if self.column is not None:
                location += f":{self.column}"
        return f"{location}: {self.kind}: {self.message}"
