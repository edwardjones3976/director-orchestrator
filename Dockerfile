FROM python:3.12-slim

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

