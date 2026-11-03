# Image for running Spot Mobile on Spot's CORE I/O (linux/arm64).
# Built by build_extension.py; see README.md.
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY static ./static

ENV PYTHONUNBUFFERED=1
CMD ["python", "server.py"]
