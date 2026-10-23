PYTHON ?= python3
export PYTHONPATH := src

.PHONY: test validate estimate mock
test:
	$(PYTHON) -m unittest discover -s tests -v
validate:
	$(PYTHON) -m collusion_recovery validate
estimate:
	$(PYTHON) -m collusion_recovery estimate --config configs/mock.toml
mock:
	$(PYTHON) -m collusion_recovery run --config configs/mock.toml
