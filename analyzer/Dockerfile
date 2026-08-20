FROM python:3.11-slim

# System deps: ghostscript (Camelot), tesseract (OCR fallback), java (Tabula), fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    ghostscript \
    tesseract-ocr \
    openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY analyzer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# repo root: shared core/ + analyzer/
COPY core/ core/
COPY analyzer/ analyzer/

WORKDIR /app/analyzer

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=5)" || exit 1

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]