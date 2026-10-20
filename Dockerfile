FROM python:3.12-slim

WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY openapi ./openapi
COPY codegen ./codegen
COPY scripts ./scripts
COPY app ./app

# Code generation is part of the build: app/generated is never committed.
RUN sh scripts/generate.sh

EXPOSE 8000
CMD ["python", "-m", "app"]
