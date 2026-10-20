#!/usr/bin/env sh
# Regenerates app/generated (models + routers) from openapi/openapi.yaml.
# Idempotent: the output directory is wiped first. Run at build time (see Dockerfile) or via `make generate`.
set -eu
cd "$(dirname "$0")/.."
rm -rf app/generated
fastapi-codegen \
  --input openapi/openapi.yaml \
  --output app/generated \
  --generate-routers \
  --template-dir codegen/templates \
  --custom-visitor codegen/visitors.py \
  --output-model-type pydantic_v2.BaseModel \
  --python-version 3.12 \
  --strict-nullable \
  --disable-timestamp
touch app/generated/__init__.py
# Generated models inherit from app.base.ApiModel (Decimal -> JSON number, as the contract says `type: number`).
sed -i 's/^from pydantic import (/from app.base import ApiModel\nfrom pydantic import (/; s/(BaseModel):/(ApiModel):/' app/generated/models.py
echo "generated: $(ls app/generated app/generated/routers | tr '\n' ' ')"
