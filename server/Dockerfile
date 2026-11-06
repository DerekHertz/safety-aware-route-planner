FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
# read at run time: the preset trips and the recorded OSRM, Overpass and Jev responses
COPY trips.json ./
COPY fixtures ./fixtures
RUN uv sync --frozen --no-dev
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "gapcross.app:app", "--host", "0.0.0.0", "--port", "8000"]
