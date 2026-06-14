FROM python:3.12-slim

# ── Node.js (required for MCP servers launched via npx) ──────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . user_agent/
WORKDIR /app/user_agent

# ── Python dependencies ───────────────────────────────────────────────
RUN pip install --upgrade pip && \
    if [ -f requirements.txt ]; then pip install -r requirements.txt; fi

EXPOSE 8088
CMD ["python", "main.py"]
