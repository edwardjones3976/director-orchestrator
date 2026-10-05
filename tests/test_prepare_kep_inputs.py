"""Register the existing synthetic KEP constructor suite in ordinary CI discovery."""
import importlib.util
from pathlib import Path
import unittest


def load_tests(loader, tests, pattern):
    path = Path(__file__).resolve().parents[1] / "projects/homemade-llm/eval/test_prepare_kep_inputs.py"
    spec = importlib.util.spec_from_file_location("kep_constructor_synthetic_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # A fresh loader avoids double-accounting this nested module in the CI
    # sharder, which records the returned suite under this discovery shim.
    return unittest.TestLoader().loadTestsFromModule(module)
