FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ESGLOCAL_HOME=/data \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

RUN useradd --create-home --uid 1000 esg && mkdir /data && chown esg /data
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[all]"

USER esg
VOLUME ["/data"]
EXPOSE 8501
# Inside the container we bind 0.0.0.0; docker-compose publishes it on 127.0.0.1 only.
CMD ["python", "-m", "streamlit", "run", "/usr/local/lib/python3.12/site-packages/esglocal/ui/app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", \
     "--browser.gatherUsageStats=false", "--server.enableXsrfProtection=true", \
     "--server.maxUploadSize=50"]
