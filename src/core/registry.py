"""Discover trusted Python strategy files through their explicit MODEL exports."""
from dataclasses import dataclass
import hashlib
import importlib.util
import inspect
from pathlib import Path
import re
import sys

from core.contracts import Model

DEFAULT_MODEL_ID = "cross_sectional"
MODELS_DIRECTORY = Path(__file__).resolve().parents[1] / "models"


@dataclass(frozen=True)
class ModelDefinition:
    name: str
    description: str
    model_type: type[Model]

    def __post_init__(self):
        if not isinstance(self.name, str) or not 1 <= len(self.name.strip()) <= 100:
            raise ValueError("Model name must contain 1–100 characters")
        if not isinstance(self.description, str) or not 1 <= len(self.description.strip()) <= 1000:
            raise ValueError("Model description must contain 1–1000 characters")
        if not isinstance(self.model_type, type) or not issubclass(self.model_type, Model):
            raise TypeError("model_type must implement core.contracts.Model")
        if self.model_type.__abstractmethods__:
            raise TypeError("Model must implement every abstract method")
        inspect.signature(self.model_type).bind()


class UnknownModel(ValueError):
    pass


class ModelRegistry:
    def __init__(self, directory=MODELS_DIRECTORY):
        self.directory = Path(directory).resolve()

    def discover(self):
        definitions = {}
        unavailable = []
        for path in sorted(self.directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            if not re.fullmatch(r"[a-z][a-z0-9_]*", path.stem):
                unavailable.append({"file": path.name, "reason": "Use a lowercase Python module filename"})
                continue
            if self.directory == MODELS_DIRECTORY:
                module_name = "models." + path.stem
            else:
                module_name = "_autotrader_plugin_" + hashlib.sha256(str(path).encode()).hexdigest()[:20]
            try:
                if module_name in sys.modules:
                    module = sys.modules[module_name]
                else:
                    spec = importlib.util.spec_from_file_location(module_name, path)
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    try:
                        spec.loader.exec_module(module)
                    except (Exception, SystemExit):
                        sys.modules.pop(module_name, None)
                        raise
                definition = module.MODEL
                if not isinstance(definition, ModelDefinition):
                    raise TypeError("MODEL must be a ModelDefinition")
                definitions[path.stem] = definition
            except (Exception, SystemExit) as error:
                unavailable.append({"file": path.name, "reason": "Cannot load MODEL definition: " + type(error).__name__})
        return definitions, unavailable

    def catalog(self):
        definitions, unavailable = self.discover()
        return {
            "models": [{"id": identifier, "name": definition.name,
                        "description": definition.description, "file": identifier + ".py"}
                       for identifier, definition in definitions.items()],
            "unavailable": unavailable,
        }

    def require(self, identifier):
        definitions, _ = self.discover()
        if identifier not in definitions:
            raise UnknownModel("Selected strategy is unavailable; choose a model listed in the dashboard")
        return definitions[identifier]

    def create(self, identifier):
        return self.require(identifier).model_type()
