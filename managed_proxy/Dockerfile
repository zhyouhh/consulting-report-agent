FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py __init__.py ./

RUN useradd --create-home --uid 10001 proxyuser
USER proxyuser

EXPOSE 18731

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "18731", "--workers", "1"]
