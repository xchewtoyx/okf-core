from __future__ import annotations

from pathlib import Path
from typing import Any
import pytest


@pytest.fixture(autouse=True)
def _force_lf_write_text(monkeypatch: pytest.MonkeyPatch) -> None:
    original_write_text = Path.write_text

    def new_write_text(self: Path, data: str, *args: Any, **kwargs: Any) -> Any:
        # Force LF line endings
        kwargs["newline"] = "\n"
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", new_write_text)
