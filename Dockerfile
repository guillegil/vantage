# The vantage server, built from this checkout:
#
#   docker build -t vantage .
#   docker run -d --name vantage -p 8765:8765 -v vantage-data:/data vantage
#
# `vantage[server,postgres]` exactly as uv.lock pins it, run as a fixed
# unprivileged user, storing in SQLite at /data/vantage.db unless
# VANTAGE_DATABASE names a PostgreSQL database.

# One base for both stages: the environment the first builds links to its
# interpreter, so the second must have the same one.
ARG PYTHON_IMAGE=python:3.13-slim-trixie

FROM ghcr.io/astral-sh/uv:0.12.13 AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
# The base image's interpreter, never one uv downloads; bytecode compiled
# here, since the server's user cannot write it next to the code.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/opt/vantage \
    UV_COMPILE_BYTECODE=1
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages/pytest-vantage/pyproject.toml packages/pytest-vantage/
COPY packages/pytest-vantage/src packages/pytest-vantage/src
COPY packages/vantage/pyproject.toml packages/vantage/
COPY packages/vantage/src packages/vantage/src
# --locked: exactly what uv.lock pins, hashes checked; a lock that no
# longer matches the pyproject files fails the build instead of being
# resolved again. --no-editable: both workspace members are built into
# wheels and installed, so the environment needs nothing from /src.
RUN uv sync --locked --no-editable --package vantage --extra server --extra postgres

FROM ${PYTHON_IMAGE}
LABEL org.opencontainers.image.title="vantage" \
      org.opencontainers.image.description="The vantage server: stores the test runs pytest-vantage records and serves their history." \
      org.opencontainers.image.source="https://github.com/guillegil/vantage" \
      org.opencontainers.image.licenses="MIT"
# A fixed uid and gid, so a bind mount can be given to them by number.
RUN groupadd --gid 10001 vantage \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin vantage \
 && install -d -m 0700 -o 10001 -g 10001 /data
# Owned by root: the server cannot rewrite its own code.
COPY --from=build /opt/vantage /opt/vantage
# The database is named by the environment, not a flag, so `-e
# VANTAGE_DATABASE=postgresql://...` replaces it and `docker exec ...
# vantage user ...` inherits it, finding the database being served.
# Unbuffered, so whatever the server prints reaches `docker logs` at once.
ENV PATH=/opt/vantage/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    VANTAGE_DATABASE=/data/vantage.db
USER 10001:10001
# Also the working directory: with a read-only root filesystem, SQLite and
# Python put their temporary files here.
WORKDIR /data
# A new named volume takes this directory's owner and 0700 mode.
VOLUME ["/data"]
EXPOSE 8765
# A route that needs no token and never waits on the store, asked
# with the standard library, since the image has no curl: every second
# while starting, then every minute, one access-log line each.
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --start-interval=1s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/v1/capabilities', timeout=4)"]
# Exec form and no init: vantage is PID 1, gets docker stop's SIGTERM
# itself, shuts the app down, closing the store, and exits 0.
ENTRYPOINT ["vantage"]
CMD ["--host", "0.0.0.0"]
