"""Register the Hermes contract probe suite in ordinary CI discovery."""
import importlib.util
from pathlib import Path
import unittest


def load_tests(loader, tests, pattern):
    path = Path(__file__).resolve().parents[1] / "projects/homemade-llm/eval/test_hermes_contract_probe.py"
    spec = importlib.util.spec_from_file_location("hermes_contract_probe_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return unittest.TestLoader().loadTestsFromModule(module)
