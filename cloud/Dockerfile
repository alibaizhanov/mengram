FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt

# App code
COPY engine/ engine/
COPY cloud/ cloud/
COPY __init__.py .

EXPOSE 8420

CMD ["python", "-m", "cloud.api"]
