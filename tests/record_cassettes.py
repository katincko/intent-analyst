"""Remake the committed recordings in tests/cassettes/ (never run by pytest).

    uv run python tests/record_cassettes.py

The conversations come from `cassette_scenarios.py` and go through the in-memory fake, so no
network is used and no key is needed.
"""

import tempfile
from pathlib import Path

from cassette_scenarios import CASSETTES, SCENARIOS
from fake_api import FakeMessagesApi

from intent_analyst.agent import ask
from intent_analyst.db import build_database, open_readonly
from intent_analyst.recording import recording

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "synthea.duckdb"
        build_database(ROOT / "data" / "synthea", db)
        con = open_readonly(db)
        try:
            for name, (scenario, script) in SCENARIOS.items():
                api = FakeMessagesApi(list(script()))
                with recording(CASSETTES / f"{name}.json", "record"):
                    outcome = ask(
                        scenario.question,
                        client=api.client(),
                        con=con,
                        model="claude-sonnet-5-5",
                        clock=api.clock,
                    )
                assert outcome.status == scenario.status, (name, outcome)
                print(f"recorded {name}: {outcome.status}")
        finally:
            con.close()


if __name__ == "__main__":
    main()
