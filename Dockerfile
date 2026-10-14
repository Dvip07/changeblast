# API and agent host share this image (different commands in docker-compose.prod.yml).
FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Fetch tree-sitter grammars at build time, not on the first investigation.
RUN python -c "import tree_sitter_language_pack as t; t.download(['python','javascript','typescript','tsx','java','php','go','ruby','csharp'])"
COPY changeblast/ changeblast/
COPY fixtures/ fixtures/
COPY scripts/ scripts/
COPY evals/ evals/
ENV FIXTURES_DIR=/data/repos REPOS_DIR=/data/repos PYTHONUNBUFFERED=1
RUN useradd -m -u 1000 app && mkdir -p /data/repos && chown -R app /data /app
USER app
CMD ["sh", "-c", "./scripts/build_repos.sh >/dev/null && uvicorn changeblast.api:app --host 0.0.0.0 --port 8100"]
