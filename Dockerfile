# Business Card Extractor — NiceGUI app
# Build:  docker compose build
#   (or)  docker build -t bc-extractor-app .
FROM python:3.11-slim

WORKDIR /app

# Install Python deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code (uploads/ and output/ are created automatically by config.py)
COPY app.py config.py extractor.py exporter.py ./

ENV HOST=0.0.0.0 \
    PORT=8090

EXPOSE 8090

CMD ["python", "app.py"]
