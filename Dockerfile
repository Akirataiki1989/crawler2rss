FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py run_one.py ./
COPY model ./model
COPY scrapers ./scrapers
COPY utils ./utils
ENV PYTHONUNBUFFERED=1 TZ=Asia/Taipei RSS_OUTPUT_DIR=/rss SCRAPER_LOG_DIR=/logs
CMD ["python", "main.py"]
