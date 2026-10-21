FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY server.py /app/server.py
COPY public /app/public
EXPOSE 10000
CMD ["python", "/app/server.py"]
