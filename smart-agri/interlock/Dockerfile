FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 5001
# Single worker: the interlock keeps lockout state in memory, so it must not
# be spread across processes.
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:5001", "--access-logfile", "-", "app:app"]
