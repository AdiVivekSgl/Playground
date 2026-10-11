FROM python:3.12-slim

WORKDIR /app
COPY relay.py .

# Run unprivileged; the relay needs no filesystem writes.
RUN useradd --system --uid 10001 relay
USER relay

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=3)"

CMD ["python", "-I", "relay.py"]
