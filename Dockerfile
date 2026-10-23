# SPEED KPI dashboard: one image for both services in compose.yaml
#   dashboard - serve_dashboard.py (ECEWS sign-in, the page and its data files)
#   job       - scripts/scheduler.py (pulls DHIS2, the CDR line list and the NDR file on a schedule, writes live/)
# Credentials come from the server's .env at run time; data lives in mounted folders, never in the image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 TZ=Africa/Lagos
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home --uid 1000 speed \
    && mkdir -p live data inbox logs archive exports \
    && chown -R speed:speed /app
USER speed

EXPOSE 5100
CMD ["python", "serve_dashboard.py", "--lan", "--port", "5100", "--no-browser"]
