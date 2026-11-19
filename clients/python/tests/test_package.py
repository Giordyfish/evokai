from __future__ import annotations

import re
from pathlib import Path

import pytest

import evokai
from evokai.models import UsageReport, to_usage_shares

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def test_version_matches_pyproject() -> None:
    text = (ROOT / "pyproject.toml").read_text()
    match = re.search(r'^version = "([^"]+)"$', text, re.MULTILINE)
    assert match is not None
    assert evokai.__version__ == match.group(1)
    # Published to PyPI by the release workflow (docs/releasing.md).
    assert '"Private :: Do Not Upload"' not in text


def test_typed_marker_ships() -> None:
    assert (ROOT / "src" / "evokai" / "py.typed").exists()


@pytest.mark.parametrize("name", ["LICENSE", "NOTICE"])
def test_legal_files_match_the_repository(name: str) -> None:
    if not (REPO / name).exists():
        pytest.skip("not inside the repository")
    assert (ROOT / name).read_text() == (REPO / name).read_text()


def test_no_em_dash_in_the_package() -> None:
    for path in [*ROOT.glob("src/**/*.py"), *ROOT.glob("tests/*.py"), ROOT / "README.md"]:
        assert chr(0x2014) not in path.read_text(encoding="utf-8"), path


def test_public_names_resolve() -> None:
    for name in evokai.__all__:
        assert getattr(evokai, name) is not None


def test_usage_shares_drop_every_amount() -> None:
    report = UsageReport.model_validate({
        "rows": [
            {"subject": "a", "costUsd": 2.0, "inputTokens": 5},
            {"subject": "b", "costUsd": 0.5},
        ],
        "application": {"monthlyCapUsd": 10, "dailyCapUsd": 0, "monthUsd": 4, "dayUsd": 1},
    })
    shares = to_usage_shares(report)
    dumped = shares.model_dump_json()
    assert "usd" not in dumped.lower()
    assert "cost" not in dumped.lower()
    assert [r.cap_pct for r in shares.rows] == [20.0, 5.0]
    assert shares.rows[0].input_tokens == 5
    assert (shares.month_pct, shares.day_pct) == (40.0, 100.0)
    no_cap = to_usage_shares(UsageReport.model_validate({"rows": [{"costUsd": 1}]}))
    assert no_cap.rows[0].cap_pct is None
    assert no_cap.month_pct is None
