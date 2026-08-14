FROM python:3.12-slim@sha256:dd29372629eeba2dd003fd9e9d35a5b8236c44727875a0364254b5127af88e65

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN addgroup --system director && adduser --system --ingroup director director

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY prompts ./prompts

RUN mkdir -p /data && chown -R director:director /app /data
USER director

EXPOSE 8080
CMD ["uvicorn", "director_orchestrator.main:app", "--host", "0.0.0.0", "--port", "8080"]

