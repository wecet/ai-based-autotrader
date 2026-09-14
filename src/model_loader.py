"""Import the existing research module with its legacy filename."""
import importlib.util
from pathlib import Path
import sys


def load_model():
    name = "cross_sectional"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / "models/cross-sectional.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


