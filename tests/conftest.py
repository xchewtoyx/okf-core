from __future__ import annotations

from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def _force_lf_write_text(monkeypatch: pytest.MonkeyPatch) -> None:
    original_write_text = Path.write_text

    def new_write_text(
        self: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        # Force LF line endings unless the caller explicitly requests otherwise
        return original_write_text(
            self,
            data,
            encoding=encoding,
            errors=errors,
            newline=newline if newline is not None else "\n",
        )

    monkeypatch.setattr(Path, "write_text", new_write_text)
