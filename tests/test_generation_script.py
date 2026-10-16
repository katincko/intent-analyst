"""The data is only reproducible while the generation script keeps every pinned input."""

import re
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent / "scripts" / "generate_data.sh").read_text(
    encoding="utf-8"
)
JAVA_COMMAND = SCRIPT[SCRIPT.index("java -jar") :]


@pytest.mark.parametrize(
    "flag",
    [
        r'-s "\$SEED"',
        r'-cs "\$CLINICIAN_SEED"',
        r'-r "\$REFERENCE_DATE"',
        r'-e "\$END_DATE"',
        r'-p "\$POPULATION"',
        r"--generate\.thread_pool_size=1\b",
    ],
)
def test_synthea_command_keeps_the_pinned_flag(flag: str) -> None:
    assert re.search(flag, JAVA_COMMAND), flag


@pytest.mark.parametrize("variable", ["SEED", "CLINICIAN_SEED", "POPULATION"])
def test_seeds_and_population_are_fixed_numbers(variable: str) -> None:
    assert re.search(rf"^{variable}=\d+\b", SCRIPT, re.MULTILINE)


def test_end_date_is_the_reference_date() -> None:
    ref = re.search(r"^REFERENCE_DATE=(\d{8})", SCRIPT, re.MULTILINE)
    assert ref
    assert re.search(r"^END_DATE=\$REFERENCE_DATE\b", SCRIPT, re.MULTILINE)


def test_docker_image_is_pinned_by_digest() -> None:
    assert re.search(r'^IMAGE="[^"@:]+@sha256:[0-9a-f]{64}"$', SCRIPT, re.MULTILINE)


def test_jar_is_pinned_by_version_and_checksum() -> None:
    assert re.search(r'^SYNTHEA_VERSION="v\d+\.\d+\.\d+"$', SCRIPT, re.MULTILINE)
    assert re.search(r'^JAR_SHA256="[0-9a-f]{64}"$', SCRIPT, re.MULTILINE)
    assert "sha256sum -c" in SCRIPT
