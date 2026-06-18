FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt ib_insync oandapyV20
COPY . .
ENV PYTHONPATH=/app
# default: dry-run. Add --live in compose/cmd to send orders.
CMD ["python", "scripts/run.py", "config/config.yaml"]
