# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:35 UTC | BEGIN]]
FROM node:22.22.0-bookworm-slim@sha256:dd9d21971ec4395903fa6143c2b9267d048ae01ca6d3ea96f16cb30df6187d94 AS firecrawl
RUN npm install --global --ignore-scripts --no-audit --no-fund firecrawl-cli@1.23.3

FROM python:3.12-slim@sha256:dd29372629eeba2dd003fd9e9d35a5b8236c44727875a0364254b5127af88e65
ARG DIRECTOR_CANDIDATE_COMMIT=unverified
ARG DIRECTOR_CANDIDATE_TREE=unverified
LABEL org.director.candidate.commit=$DIRECTOR_CANDIDATE_COMMIT \
      org.director.candidate.tree=$DIRECTOR_CANDIDATE_TREE

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# Keep npm and build-stage caches out of the runtime; no provider login at build time.
COPY --from=firecrawl /usr/local/bin/node /usr/local/bin/node
COPY --from=firecrawl /usr/local/lib/node_modules/firecrawl-cli /usr/local/lib/node_modules/firecrawl-cli
RUN ln -s /usr/local/lib/node_modules/firecrawl-cli/dist/index.js /usr/local/bin/firecrawl

RUN addgroup --system director && adduser --system --ingroup director director

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY docs/schemas ./docs/schemas
COPY prompts ./prompts

RUN mkdir -p /data && chown -R director:director /app /data
USER director

# Exercise the existing public-search adapter's CLI contract without credentials.
RUN test "$(firecrawl --version)" = "1.23.3" \
    && firecrawl search --help > /tmp/firecrawl-help \
    && grep -q -- '--limit' /tmp/firecrawl-help \
    && grep -q -- '--no-highlights' /tmp/firecrawl-help \
    && grep -q -- '--json' /tmp/firecrawl-help \
    && rm /tmp/firecrawl-help

EXPOSE 8080
CMD ["uvicorn", "director_orchestrator.main:app", "--host", "0.0.0.0", "--port", "8080"]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:35 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:12 UTC | END]]
