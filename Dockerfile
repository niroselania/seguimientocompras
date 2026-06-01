FROM python:3.12-slim

WORKDIR /app
COPY . /app

ENV PORT=8080
ENV DATA_DIR=/data

EXPOSE 8080
VOLUME ["/data"]

CMD ["python", "server.py"]
