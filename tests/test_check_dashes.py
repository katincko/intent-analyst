import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_dashes.py"
EM = chr(0x2014)
EN = chr(0x2013)


def run(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), *map(str, paths)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_clean_file_passes(tmp_path: Path) -> None:
    clean = tmp_path / "a.md"
    clean.write_text("plain text, a hyphen - and a colon:\n", encoding="utf-8")
    assert run(clean).returncode == 0


def test_em_dash_fails_with_file_and_line(tmp_path: Path) -> None:
    bad = tmp_path / "a.md"
    bad.write_text(f"fine\nbad {EM} here\n", encoding="utf-8")
    result = run(bad)
    assert result.returncode == 1
    assert f"{bad}:2: em dash (U+2014)" in result.stdout


def test_en_dash_fails(tmp_path: Path) -> None:
    bad = tmp_path / "a.md"
    bad.write_text(f"pages 1{EN}2\n", encoding="utf-8")
    result = run(bad)
    assert result.returncode == 1
    assert "en dash (U+2013)" in result.stdout


def test_binary_file_is_skipped(tmp_path: Path) -> None:
    blob = tmp_path / "a.bin"
    blob.write_bytes(b"\xff\xfe\x00\x81")
    assert run(blob).returncode == 0


def test_repository_has_no_dash() -> None:
    """The real gate: git ls-files over this repository."""
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout
