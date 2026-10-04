# For Hugging Face Spaces (and any Docker host).
#
# Spaces requires the app to listen on port 7860 - not negotiable, the platform
# probes that port and marks the Space unhealthy otherwise.
FROM python:3.12-slim

# Unbuffered output so logs appear in the Space's log pane as they happen.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=7860 \
    HOST=0.0.0.0 \
    OUTREACH_HOSTED=1 \
    OUTREACH_DB=/data/outreach.db

WORKDIR /app

# Dependencies first so code edits don't invalidate the pip layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# /data is where the database lives. On free Spaces this is ephemeral (wiped on
# rebuild, survives sleep), so use the app's Backup button before a redeploy.
RUN mkdir -p /data && chmod 777 /data

# Spaces runs containers as a non-root user; make sure it can write.
RUN useradd -m -u 1000 appuser && chown -R appuser /app /data
USER appuser

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
