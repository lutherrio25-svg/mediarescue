# Run the CLI without installing Python or ffmpeg on the host:
#   docker run --rm -v "D:/broken:/media" ghcr.io/lutherrio25-svg/mediarescue:latest scan /media
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir .

# Repairs write into a mounted folder; never run against the image's own filesystem.
WORKDIR /media
ENTRYPOINT ["mediarescue"]
CMD ["--help"]
