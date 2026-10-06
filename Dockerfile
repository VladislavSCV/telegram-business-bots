FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml ./
COPY bot ./bot
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build /install /usr/local
COPY bot ./bot
COPY knowledge ./knowledge
RUN mkdir -p /app/data && chown app:app /app/data
USER app
VOLUME ["/app/data"]
# The bot only makes outgoing connections; the check verifies the process can import and read its config.
HEALTHCHECK --interval=60s --timeout=10s --retries=3 CMD python -c "import bot.main" || exit 1
CMD ["python", "-m", "bot.main"]
