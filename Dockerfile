# whatdvd：Debian 12 + Python 3.12，以普通用户运行，不需要 privileged，也不 mount ISO。
FROM python:3.12-slim-bookworm AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg mediainfo p7zip-full mktorrent \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels \
    && useradd --create-home --uid 1000 whatdvd \
    && mkdir -p /config /output \
    && chown whatdvd:whatdvd /output
COPY docker/config.toml /config/config.toml

USER whatdvd
ENV PYTHONUNBUFFERED=1 \
    WHATDVD_CONFIG=/config/config.toml
EXPOSE 26873
VOLUME ["/output"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:26873/', timeout=4)"
ENTRYPOINT ["whatdvd"]
# 容器内必须监听 0.0.0.0 才能被端口映射访问；对外只映射到宿主机 127.0.0.1，见 docker-compose.yml
CMD ["serve", "--host", "0.0.0.0"]
