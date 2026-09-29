# BookFlow

A small, self-hosted OPDS server: read-only filesystem → searchable index → OPDS catalog,
with on-demand EPUB optimization and single-user reading progression.

See [plan.md](plan.md) for the full implementation plan.

## Requirements

- [uv](https://docs.astral.sh/uv/)
- Python 3.14+ (managed automatically by uv)

## Setup

```bash
uv sync
```

## Configuration

All settings come from environment variables (defaults in parentheses):

| Variable                 | Default     | Description                          |
| ------------------------ | ----------- | ------------------------------------ |
| `OPDS_HOST`              | `0.0.0.0`   | Bind address                         |
| `OPDS_PORT`              | `8000`      | Bind port                            |
| `OPDS_DATA_DIR`          | `./data`    | Data directory (SQLite + cache)      |
| `OPDS_DATABASE_URL`      | derived     | Override the SQLAlchemy database URL |
| `OPDS_SESSION_SECRET`    | *(dev key)* | Flask session secret                 |
| `OPDS_ADMIN_USERNAME`    | `admin`     | Admin username                       |
| `OPDS_ADMIN_PASSWORD`    | *(empty)*   | Admin password                       |
| `OPDS_SESSION_COOKIE_SECURE` | `false` | Set the session cookie `Secure` flag (enable behind HTTPS) |

## Admin UI

Set `OPDS_ADMIN_PASSWORD` and open `/admin/login`. Admin routes require a
session; OPDS routes (added in later milestones) use HTTP Basic Auth with the
same credentials. Without `OPDS_ADMIN_PASSWORD` the login form returns `503`.

## Development

Run the development server:

```bash
uv run flask --app bookflow.app run
```

Apply database migrations:

```bash
uv run alembic upgrade head
```

Create a new migration:

```bash
uv run alembic revision --autogenerate -m "description"
```

Run tests:

```bash
uv run pytest
```

Lint:

```bash
uv run ruff check .
```

## Production

```bash
uv run gunicorn --bind 0.0.0.0:8000 bookflow.app:app
```

Mount library directories read-only; only the data directory needs write access.
