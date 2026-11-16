FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 5001

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5001"]