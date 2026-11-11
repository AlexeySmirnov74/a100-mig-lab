FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir requests==2.34.2
COPY smoke_test.py /app/smoke_test.py
CMD ["python", "/app/smoke_test.py"]
