FROM python:3.12-slim

WORKDIR /app
COPY . /app

ENV PORT=7070
ENV DATA_DIR=/data

EXPOSE 7070
VOLUME ["/data"]

CMD ["python", "server.py"]
