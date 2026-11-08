"""Error type shared by all domain modules (stable `code` for clients)."""

from __future__ import annotations


class ProjectError(Exception):
    """A project cannot be read. `code` is stable and machine-readable."""

    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        text = f"[{self.code}] {self.message}"
        return f"{text} Hint: {self.hint}" if self.hint else text
