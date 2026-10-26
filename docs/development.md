# Development

Everything needed to run VolleyFlow on your own machine. The README has
the short version.

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20+ (frontend tests and browser checks)
- A PostgreSQL database. The project uses a [Neon](https://neon.tech)
  branch, but any Postgres works.

## First run

```bash
git clone https://github.com/marksu1104/volleyflow.git
cd volleyflow
uv sync
cp .env.example .env          # then set DATABASE_URL
uv run alembic upgrade head
```

Then start the API and the pages, each in its own terminal.

On Windows PowerShell:

```powershell
.\scripts\dev-api.ps1     # the API on :8000, with local sign-in on
.\scripts\dev-web.ps1     # the pages on :5500
```

On bash or zsh:

```bash
VOLLEYFLOW_DEV_LOGIN=1 DEVELOPER_LINE_USER_ID=dev-nobody \
INVITE_TOKEN_SECRET=dev-local-secret \
  uv run uvicorn volleyflow.api.main:app --host 0.0.0.0 --port 8000
cd frontend && python -m http.server 5500
```

`DEVELOPER_LINE_USER_ID` and `INVITE_TOKEN_SECRET` are placeholders for
the two settings production holds real values for; without them invite
links answer 503 and the problem-report page says it is not configured.

Set `VOLLEYFLOW_DOCS=1` as well to get the interactive API docs at
<http://localhost:8000/docs>. They are off by default because they map
the whole API.

A page talks to the API on whatever host served it, as long as that host
is this machine or a private network address; anywhere else it talks to
production (`apiBase()` in `frontend/js/common/api.js`).

## Sample data

The database starts empty, so the app opens on "no club yet".

```bash
uv run python scripts/seed_dev.py
```

This builds a full season through the HTTP API: a roster of 18, 13
games, leave with and without a substitute, a guest somebody brought, a
night whose air-conditioning forecast was wrong, and members who have
paid, part paid and overpaid. It goes through the API rather than
inserting rows, so every ledger entry is written by the real route
handlers. Re-running it replaces its own club.

Deleting a club never deletes players (a player is global), so repeated
seeding leaves people behind:

```bash
uv run python scripts/tidy_dev_db.py          # count them
uv run python scripts/tidy_dev_db.py --yes    # delete them
```

It removes only a player in no club with nothing pointing at them, and
needs `VOLLEYFLOW_DEV_LOGIN=1` before it deletes anything.

## Signing in without LINE

Every page needs a verified identity, and a laptop has no LINE app to
provide one. `?as=<name>` signs you in as that person for the browser
session; `?as=` with nothing after it signs out.

```
http://localhost:5500/member.html?as=周恆        # a member's view
http://localhost:5500/organizer.html?as=周恆     # the same person, managing
```

It takes three things at once, so it cannot be opened by accident:

| Lock | Where |
|---|---|
| `VOLLEYFLOW_DEV_LOGIN=1` must be set | the server's environment; production never sets it |
| the token must look like `dev:<name>` | `api/auth.py`; nothing LINE issues looks like that |
| the page must be served from this machine or the local network | `frontend/js/common/api.js` |

These identities are stored with a `dev:` prefix, so they can never
collide with a real LINE account. See `tests/test_auth.py`.

## Testing on a phone

A desktop browser cannot show Safari's rendering, touch targets or the
on-screen keyboard. Both scripts print a `192.168.x.x` address; open it
from a phone on the same wifi:

```
http://192.168.1.101:5500/member.html?as=周恆
```

Allow Python through the Windows firewall for private networks only.
While the scripts run, anyone on the same wifi can sign in as anyone in
the development database, which is why this lives in a script and never
in a deployment.

The LINE-specific parts (LIFF sign-in, the rich menu) can only be tested
inside the LINE app itself.

## Checks

The same checks CI runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src scripts
uv run lint-imports
uv run pytest
node --test tests/frontend/*.test.js
```

`uv run pytest -m postgres` runs the tests that need a real Postgres.
The browser checks under `tests/visual/` need both local servers and
sample data; see [tests/visual/README.md](../tests/visual/README.md).
