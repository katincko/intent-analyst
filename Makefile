.PHONY: test lint secrets dashes

# Names every test file instead of passing the directory.
TEST_FILES := $(sort $(wildcard tests/test_*.py))

test:
	uv run pytest $(TEST_FILES) -q

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

# Pinned gitleaks over the whole history and the working tree.
secrets:
	bash scripts/gitleaks.sh

# No em dash or en dash in any tracked text file.
dashes:
	uv run python scripts/check_dashes.py
