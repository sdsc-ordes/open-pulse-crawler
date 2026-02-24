# Deployment Guide

## Docker (Recommended)

The project ships a multi-stage `Dockerfile` at the repository root that
produces a small production image based on `python:3.12-slim`.

### Building the image

```bash
docker build -t open-pulse-crawler .
```

### Running the container

```bash
docker run -d \
  --name opc-api \
  -p 8000:8000 \
  -e GITHUB_TOKEN="ghp_..." \
  -e API_TOKEN="my-secret-api-token" \
  open-pulse-crawler
```

The API will be available at `http://localhost:8000`. Visit
`http://localhost:8000/api/v1/health` to verify it is running.

### Environment variables

| Variable       | Required | Description                                       |
| -------------- | -------- | ------------------------------------------------- |
| `GITHUB_TOKEN` | Yes      | GitHub personal access token(s), comma-separated. |
| `API_TOKEN`    | Yes      | Bearer token required for protected endpoints.    |

You can pass an env file instead:

```bash
docker run -d --env-file .env -p 8000:8000 open-pulse-crawler
```

### Customising uvicorn

Override the default CMD to change host, port, or worker count:

```bash
docker run -d \
  -p 8000:8000 \
  --env-file .env \
  open-pulse-crawler \
  --host 0.0.0.0 --port 8000 --workers 4
```

### Image details

| Property     | Value                            |
| ------------ | -------------------------------- |
| Base image   | `python:3.12-slim`               |
| Build tool   | `uv` (builder stage)             |
| Entrypoint   | `uvicorn open_pulse_crawler.api:app` |
| Exposed port | `8000`                           |
| Run user     | `app` (UID 1000, non-root)       |

## Running without Docker

```bash
# Install the package (with uv or pip)
uv pip install -e ".[dev]"

# Start the API server
uvicorn open_pulse_crawler.api:app --host 0.0.0.0 --port 8000
```

Set `GITHUB_TOKEN` and `API_TOKEN` in your environment or a `.env` file before
starting the server.
