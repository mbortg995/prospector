.PHONY: venv install test lint fmt clean

VENV := .venv
PY   := $(VENV)/bin/python

venv:
	python3 -m venv $(VENV)

install: venv
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -e ".[dev]"

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check .

fmt:
	$(PY) -m ruff check --fix .

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache **/__pycache__ *.egg-info
