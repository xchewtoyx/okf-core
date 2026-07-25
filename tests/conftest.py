from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import settings

# CI runners are slower and less consistent than local machines, so a
# deadline tuned for local speed produces flaky failures under load rather
# than catching real performance regressions. Disable the deadline entirely
# for the whole suite instead of tuning a number that would just need
# raising again the next time CI gets slower.
settings.register_profile("ci", deadline=None)
settings.load_profile("ci")


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
