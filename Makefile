.PHONY: test lint dashes

# Names every test file instead of passing the directory.
TEST_FILES := $(sort $(wildcard tests/test_*.py))

test:
	uv run pytest $(TEST_FILES) -q

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

# No em dash or en dash in any tracked text file.
dashes:
	uv run python scripts/check_dashes.py
