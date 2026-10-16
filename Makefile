.PHONY: test lint

# Names every test file instead of passing the directory.
TEST_FILES := $(sort $(wildcard tests/test_*.py))

test:
	uv run pytest $(TEST_FILES) -q

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
