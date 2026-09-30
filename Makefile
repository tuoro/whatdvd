PYTHON ?= .venv/bin/python

.PHONY: dev test unit typecheck sample clean

dev:
	python3 -m venv .venv
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest

unit:
	$(PYTHON) -m pytest -m "not integration"

typecheck:
	$(PYTHON) -m mypy --strict src

sample:
	$(PYTHON) tests/dvdgen.py sample-dvd

clean:
	rm -rf whatdvd-output sample-dvd .pytest_cache build dist
