# A self-hosted copy of the app (docs/self-hosting.md). It ships no BSData or Wahapedia files:
# the Setup page downloads them into the data volume, as each desktop user does.
#   docker build -t combat-sim .
#   docker run -p 5000:5000 -v combat-sim-data:/data combat-sim
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COMBAT_SIM_HOSTED=1 \
    COMBAT_SIM_DATA=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY run.py .
COPY src ./src
# Seeds the data volume on first start; never overwritten afterwards
COPY data/app_data.json ./data/app_data.json

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data && chown app:app /data
USER app
VOLUME /data
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/setup', timeout=4)"

CMD ["python", "run.py", "--hosted", "--port", "5000"]
