"""Custom visitor for fastapi-code-generator.

Adds template variables derived from the OpenAPI spec:
  call_args[function_name]  -> "body=body, id=id"  (keyword args forwarded to the implementation)
  roles[function_name]      -> ["SELLER", "ADMIN"] (from the x-roles extension)
  success_code[function_name] -> 201 / 200 (first 2xx response of the operation)
"""
from pathlib import Path
from typing import Dict

from fastapi_code_generator.parser import OpenAPIParser, snakecase


def visit(parser: OpenAPIParser, model_path: Path) -> Dict[str, object]:
    call_args: Dict[str, str] = {}
    roles: Dict[str, list] = {}
    success_code: Dict[str, int] = {}

    raw_paths = parser.raw_obj.get("paths", {})
    for op in parser.operations.values():
        names = [snakecase(a.name) for a in op.arguments_list]
        call_args[op.function_name] = ", ".join(f"{n}={n}" for n in names)

        raw_op = raw_paths.get(op.path, {}).get(op.method.lower(), {})
        roles[op.function_name] = raw_op.get("x-roles", [])

        codes = [int(c) for c in op.responses if str(c).startswith("2")]
        success_code[op.function_name] = codes[0] if codes else 200

    return {"call_args": call_args, "roles": roles, "success_code": success_code}
