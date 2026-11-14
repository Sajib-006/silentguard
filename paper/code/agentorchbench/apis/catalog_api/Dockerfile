FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python app.py --help 2>/dev/null || python -c "from app import init_db; init_db()"
CMD ["gunicorn", "--bind", "0.0.0.0:8003", "--workers", "2", "--preload", "app:app"]
