FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/backend
COPY backend/pyproject.toml ./pyproject.toml
RUN pip install --upgrade pip && pip install ".[dev]"
COPY backend/ /app/backend/
COPY 上海市五四制初中数学知识图谱.html /app/上海市五四制初中数学知识图谱.html

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
