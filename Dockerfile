FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RESOURCEOPS_TRACE_DB=/app/var/resourceops.sqlite3 \
    RESOURCEOPS_APPROVAL_STORE=/app/var/approvals.jsonl

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/var

EXPOSE 18000

CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "18000"]
