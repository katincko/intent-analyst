import shutil
import socket
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from intent_analyst.db import build_database, open_readonly

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def built_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("db") / "synthea.duckdb"
    build_database(ROOT / "data" / "synthea", path)
    return path


@pytest.fixture
def db_copy(built_db: Path, tmp_path: Path) -> Path:
    """A private copy, so a test that (wrongly) manages to write cannot damage the others."""
    path = tmp_path / "copy.duckdb"
    shutil.copy(built_db, path)
    return path


@pytest.fixture
def con(db_copy: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    connection = open_readonly(db_copy)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may open a connection. The model client is a fake transport, never a socket."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a test tried to use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
