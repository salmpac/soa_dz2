"""Base class for generated models (injected by scripts/generate.sh).

The contract declares money fields as `type: number, format: decimal`. Pydantic serializes
Decimal as a JSON *string* by default, so the JSON encoder maps Decimal -> float at
serialization time only; all business arithmetic stays in Decimal (exact, 2 fraction digits).
"""
import warnings
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)

    class ApiModel(BaseModel):
        model_config = ConfigDict(json_encoders={Decimal: float})
