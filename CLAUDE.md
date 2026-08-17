# VolleyFlow

> This file is the single source of truth for the project. Claude Code must follow its scope, rules, and working agreement.

---

## 1. Background

- Developer: Mark, master's in Industrial Engineering (NTHU), research background in BM25-based knowledge graph completion, currently job-hunting for data science / AI engineering roles.
- Why this project exists:
  1. Solve a real problem: running signups, waitlists, and billing for a weekly volleyball game organized over a LINE group.
  2. Fill resume gaps: backend engineering, databases, testing, deployment (GenAI/agents are a stretch goal, not the focus).
  3. Have something demoable and defensible in interviews by early September.
- Environment: Windows + RTX 3060 Ti, zero budget — free tiers only, nothing that requires a credit card.
- The developer is new to backend work; prior experience is limited to some PHP.

## 2. Domain rules — do not change without asking

The full, executable billing rules live in [`docs/billing-rules.md`](docs/billing-rules.md).
If this section and that file disagree, `docs/billing-rules.md` wins.

### 2.1 Vocabulary

One person maps to one name for life. A `Player` is not two different types
depending on whether they currently pay a season fee — `Membership` is a
relationship, not a kind of person. This matters because a drop-in today may
become a member next season, and their ledger history must carry over.

| Term | Meaning |
|---|---|
| `Player` | One real person. Tied to a LINE user id. Exists once, forever. |
| `Membership` | A `Player`'s fixed-member relationship to a specific `Season`. |
| `DropIn` | A non-member who signs up and pays for a single `Game`. |
| `Season` | A billing period: a fixed set of games, a total venue cost, a fixed member list. |
| `Game` | One scheduled occurrence within a `Season`. |
| `Absence` | A member skipping a `Game` they're otherwise expected to attend. |
| `cover` | A `DropIn` filling the slot an `Absence` opened up. |
| `WaitlistEntry` | A `DropIn` signup that didn't get a slot yet, in order. |
| `Settlement` | The end-of-season reckoning of who is owed what. |
| `Ledger` / `LedgerEntry` | A `Player`'s append-only history of charges and refunds. |
| `Organizer` | The person running the group and marking payments received. |

### 2.2 How a game runs

- One game per week, fixed time (usually Tuesday), capacity capped at 18 (configurable).
- Group of 20–30 people: fixed members plus drop-ins.
- A season is configurable: pay-per-use, monthly, or a full season block. When starting a season, the organizer enters: rental mode, the fixed weekday, an explicit list of dates (individually editable), the total venue cost, and the fixed member list. The system generates all games from that.

### 2.3 Signup and waitlist

- Members are expected by default; they only need to record an `Absence`, never sign up.
- A `DropIn` can sign up for any future game (+1); once full, further signups queue on the waitlist in order.
- When a member records an `Absence`, the waitlist is offered the slot in order.
- A `DropIn` can cancel; the cancellation deadline is a configurable parameter (default: no deadline).
- Games are auto-reminded before kickoff with the current roster. If the roster is short, **only the organizer is notified** — never the waitlist.

### 2.4 Billing — the core of the project; get this wrong and the project has no point

- A member's season fee is the total venue cost split evenly across members.
- A member's `Absence` is refunded one game's share **only if a `DropIn` actually covers it**.
- A `DropIn` pays the same per-game share, collected by the organizer.
- At season end, `Settlement` computes what each person is owed or still owes.
- A refund can be settled two ways: **cash**, or **carried into next season's balance** — this is why `Ledger` must span seasons.
- Payments and refunds are marked received/paid **manually by the organizer**. No payment gateway integration.
- All amounts round up to whole dollars; the rounding surplus accumulates as `surplus`, spent at the organizer's discretion.

### 2.5 Design principles

- Every rule (capacity, rates, cancellation deadlines, waitlist behavior) is a configurable parameter — not hardcoded — but the system serves exactly one group. No multi-tenancy.
- Billing is a **deterministic rules engine**. Never use an LLM to compute an amount.
- Every data change is append-only and auditable: who, when, what changed.

## 3. Tech stack — decided, do not propose alternatives

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12+ | Developer already knows it; aligns with AI engineering roles |
| Backend framework | FastAPI | Standard for AI-adjacent services; free Swagger UI |
| Database | PostgreSQL (Neon free tier) | Industry-standard; free, no card required |
| ORM | SQLAlchemy | Standard Python DB tooling |
| Frontend | Plain HTML + JavaScript, no React | Learning backend and React in six weeks at once is too much |
| Entry point | LINE LIFF (in-app webview, carries LINE identity automatically) | Users already live in LINE; zero signup friction |
| Notifications | LINE Messaging API (200 free messages/month; send to the group, not individuals, to conserve quota) | |
| Deployment | Backend on Render free tier; frontend on Vercel or GitHub Pages | Free, no card; UptimeRobot to prevent sleep |
| Scheduling | GitHub Actions `schedule` | Pre-game reminders and similar timed jobs |
| Testing | pytest | Billing logic must be fully tested |
| Dependency management | uv | Lockfile-based, reproducible environments |
| Linting/formatting | ruff | One tool, replaces black + isort + flake8 |
| Type checking | mypy --strict | |
| Version control | Git + GitHub (github.com/marksu1104) | |

## 4. Milestones — strictly one at a time, no working ahead

### Milestone 1 (8/1–8/7): billing engine — pure Python, no web, no DB, no LINE
Deliverables:
- Pure Python package: `Season`, `Game`, `Player`, `Membership`, `Ledger` data models and calculation logic.
- Input: season config + absence records + drop-in records → output: each player's charge / refund / balance.
- At least 15 pytest cases, covering:
  1. Baseline: nobody absent
  2. A single absence
  3. The same player absent multiple times
  4. An absence covered by a drop-in (drop-in pays the per-game share)
  5. A drop-in signs up then cancels
  6. Waitlist ordering is correct
  7. Balance carried across seasons
  8. Cash settlement
  9. Edge case: rounding when the split doesn't divide evenly
  10. Edge case: a game is cancelled entirely (both variants — refunded and unrefunded)

### Milestone 2 (8/8–8/14): DB + API
- Neon PostgreSQL schema design, SQLAlchemy models, Alembic migrations.
- FastAPI endpoints (start season, record absence, sign up, cancel, view settlement).
- API-layer tests.

### Milestone 3 (8/15–8/21): LIFF frontend
- Member view: this season's games, absence / +1 / cancel.
- Organizer dashboard: attendance, balances owed, one screen to read.

### Milestone 4 (8/22–8/28): LINE integration + deployment
- Messaging API: pre-game reminder to the group, short-roster alert to the organizer only.
- GitHub Actions schedule, deploy to Render, keep it awake.

### Milestone 5 (8/29–9/4): real-world launch
- Run it for a real week, fix what breaks.
- README: architecture diagram + the reasoning behind each design decision (this is the resume-facing artifact).

### Stretch goal (after 9/5, only once everything above is done)
- LLM agent: parse free-form multi-intent messages from the group (e.g. "I'm out next Tuesday, carry my balance to next season, and sign up my friend as a drop-in") and call the existing API via function calling. The LLM is an interface only, never the calculation engine. Needs PII masking and prompt-injection testing.
- **Explicitly out of scope**: RAG (not enough data to justify it), payment gateway integration, multi-tenancy, monthly reports.

## 5. Working agreement with Claude Code

- Converse with the developer in **Traditional Chinese**; keep technical terms in English.
- The developer is new to backend work: explain each new concept the first time it comes up, an analogy is fine.
- **Teaching mode**: don't generate the whole project at once. Work in small steps, explain *why* something is written the way it is, and get confirmation before moving to the next step. The point of this project is for the developer to understand it well enough to defend every design decision in an interview — a pile of code they can't explain defeats the purpose.
- Follow milestone order strictly. If the developer asks to build ahead of the current milestone, push back and explain why.
- Actively resist feature creep.
- Say plainly when a decision looks wrong. Don't just agree.
- Commit at natural stage boundaries (e.g. a module working with its tests passing), not after every intermediate step. Explaining and confirming each step is still done in conversation — it doesn't need a commit of its own.

## 6. Engineering conventions

### Language policy
- English: code, comments, docstrings, commit messages, everything under `docs/`, this file.
- Traditional Chinese: the LIFF UI and LINE messages sent to users. Keep this text centralized in one module (not scattered across the codebase) so it can be edited without touching logic.
- Traditional Chinese: conversation with the developer.

### Money
- `Decimal` only. `float` is never acceptable for money — floating-point error is fatal in a billing system.
- Rounding happens exactly once, in the per-game share calculation. See `docs/billing-rules.md`.

### Types
- Type hints everywhere. `mypy --strict`.

### Project layout
```
src/volleyflow/
├── players.py       Player, Membership
├── schedule.py       Season, Game, GameStatus
├── attendance.py     Absence, DropIn, WaitlistEntry
├── pricing.py        per-game share and rounding rules
├── settlement.py      season-end settlement engine
└── ledger.py          LedgerEntry, balance calculation
```
`src/` layout is deliberate: without it, `pytest` run from the project root can
import the package directly off disk, bypassing installation. That lets tests
pass locally while a packaging mistake only shows up in deployment. Under
`src/`, the package isn't importable until it's actually installed
(`uv sync`), so the tests exercise what deployment will actually run.

Files are named for what they're responsible for, not what data type they
hold (`pricing.py`, not `money.py`). There is no separate `domain/` folder
yet — milestone 1 has nothing to layer against. When milestone 2 introduces
SQLAlchemy, billing logic must not import it; that boundary will be enforced
with `import-linter` at that point, and only then is a `domain/` split worth
its cost.

### Testing
- pytest. One test file per source file: `tests/test_pricing.py` ↔ `src/volleyflow/pricing.py`.
- Test names: `test_<scenario>_<expected_outcome>` — readable without opening the body.
- Arrange–Act–Assert, separated by a blank line.
- Billing logic (`pricing.py`, `settlement.py`, `ledger.py`) targets 100% coverage. CI fails below that.

### Git
- Default branch: `main`.
- Workflow: feature branch → PR → CI green → merge. No direct commits to `main`.
- Commit messages: one line, plain short description of what the code now does. No `type:` prefix, no body, no internal planning terms (e.g. "milestone 1") — describe the change, not where it sits in the schedule.
- No AI attribution (e.g. `Co-Authored-By: Claude`) in commit messages.
- Commit granularity is one commit per completed, tested stage, not per file or per intermediate step. For milestone 1 that stage is the whole billing engine (all six modules, full test suite) — one commit, not six.
- While a stage is in progress, local commits are fine as a safety net (uncommitted work has no recovery path if something goes wrong). They stay local and get squashed into the single stage commit before the first push — the public history never shows the intermediate steps.
- No ADR files, no rationale in commit bodies (there are no bodies). Decision rationale lives in conversation and, eventually, `README.md` (milestone 5). Don't create standalone decision-record documents — they duplicate what the README already covers, and the duplicate goes stale first.
- The one exception: [`docs/dev-log.md`](docs/dev-log.md), a single running file appended to once per completed stage — what got built and why, key commands included. Purpose is different from the README (that's resume-facing, written once at the end): this is a trail for coming back later and re-learning how the project was actually built. Append to it, don't create a second log file.

### Tooling
- `uv` for environment and dependency management. `uv.lock` is committed.
- `ruff` for linting and formatting — one tool, not three.
- `pre-commit` runs `ruff` before every commit.
- GitHub Actions runs `ruff` → `mypy` → `pytest` on every push and PR; all three must pass.
