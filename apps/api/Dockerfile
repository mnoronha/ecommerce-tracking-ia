# Stable Python build for Railway/Render/Fly. Bypasses Railpack/Nixpacks
# auto-detection and gives deterministic, reproducible builds.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system deps (gcc for some Python packages that need to compile)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    # WeasyPrint dependencies (PDF generation)
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    libxml2 \
    libxslt1.1 \
    # Fonts for WeasyPrint — slim image ships none, so PDFs render blank (no glyphs).
    # Liberation = Arial substitute (CSS troca 'Sora'→'Arial'); DejaVu = sans-serif
    # fallback ('Inter'→sans-serif); Noto emoji = 🟢/🟡/🔴/📈 do relatório.
    fonts-liberation \
    fonts-dejavu-core \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Railway provides $PORT at runtime
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
