# AGENTS.md

Rules for every coding agent (Claude Code, Codex, others) and every human contributor
working in this repository. `CLAUDE.md` imports this file, so there is one source of truth.

## Project

CityPulse (working name) is a web application to monitor and analyse bike-share
availability. It starts with the official BIXI GBFS feed and covers every station that
feed publishes, including stations outside Montréal.

Planned stack (monorepo):

- `backend/` — Python 3.12, FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL, pytest, Ruff, Pyright
- `frontend/` — React, TypeScript (strict), Vite, Leaflet, Recharts
- `infra/` — Docker Compose
- `docs/` — architecture, data quality, source audit
- `sample-data/` — small, dated, licence-compatible raw snapshots used by tests

Scope, priorities and acceptance criteria live in the GitHub issues and milestones.

## Language

- Conversation with the project owner: French.
- Everything committed or published is in English: code, identifiers, comments, tests,
  commit messages, issues, pull requests and documentation.

## Workflow

### Roles

- **Claude Code** leads: it plans, assigns tasks, arbitrates review disagreements and
  integrates the work. It weighs Codex's work, opinions and critiques seriously; when it
  disagrees, it explains why with evidence.
- **Codex** collaborates: it reviews diffs, critiques design proposals, and implements
  issues (or clearly bounded parts of issues) assigned to it.
- **Humans** decide when the agents disagree, and approve merges, licensing decisions and
  publication.
- Both agents have exactly the same permissions and prohibitions (see "Security and
  permissions"); only their roles differ.

### Ownership and boundaries

Coordination lives in the local, gitignored `.collab/` folder. Read `.collab/HANDOFF.md`
first when it exists: its Now board shows who works on what.

- One agent owns an issue and its branch at a time, in its own git worktree. Parallel
  work is allowed only on disjoint paths.
- Before starting, the agent claims its row on the Now board and appends a `START` entry
  to `.collab/WORKLOG.md`: issue, branch and the exact paths it will touch. When it
  stops, it appends an `END` entry: what was done, files changed, checks run, what is
  left and open questions for the other agent. An agent that cannot write to `.collab/`
  gives these entries in its final message for the other agent to record.
- An agent never edits paths claimed by the other agent, nor the other agent's worktree.
- Do not mark authorship inside code or commits (no "begin/end <agent>" comments,
  no AI trailers). Boundaries are recorded in the log, at file or module level.

### Design review (substantive topics only)

For planning (task breakdown, priorities, sequencing), architecture, data model, spikes,
experiments and research directions, the other agent critiques before anything is applied:

1. The proposing agent writes `.collab/reviews/<issue>-<topic>.md`: problem, options,
   recommendation, risks.
2. The other agent appends a `Feedback` section: agreement, concerns, checks to run.
3. The proposer records the `Decision`, then implements.

Small issues skip this step and get the pull-request review only. One round per review,
unless a blocker is found.

### From issue to merge

1. Every change starts from a GitHub issue with acceptance criteria.
2. Create a short-lived branch from `main` named `<issue-number>-<short-slug>`
   (example: `4-gbfs-client`).
3. The owner implements and runs the full validation gate locally (see Commands).
4. Open a pull request that closes the issue and fill in the PR template.
5. The other agent reviews the diff read-only. Findings and their triage are recorded
   locally in `.collab/reviews/` (gitignored), not posted on the pull request; the owner
   fixes the justified findings before merge. One review pass and one fix pass, unless a
   severe defect is found.
6. CI green, then a human approves and merges (squash merge).

Rules:

- Keep pull requests small (aim for fewer than 400 changed lines, excluding lockfiles and fixtures).
- Trunk-based development: `main` stays releasable; no long-lived branches.
- Prefer deterministic scripts over agents when a script is enough.

## Security and permissions (identical for every agent)

Agents propose; humans apply. Humans merge pull requests, change repository settings,
agent permissions and hooks, and publish.

- Write only inside your own worktree, plus the machine-specific roots a human lists in
  `.agent-policy/local_roots.txt` (gitignored). Never write in another agent's worktree.
- Never edit agent permissions, hooks or policy: `.claude/settings*`, `.claude/hooks/`,
  `.codex/`, `.agent-policy/`, `.githooks/`, `.git`. To change one, write the proposed
  file under `.tmp/proposed/` and let a human apply it.
- Never bypass or disable the git hooks (`--no-verify`, `git commit -n`, changing
  `core.hooksPath`), force-push, delete remote branches, hard-reset, `git clean`, delete
  Docker volumes, call `gh api`, install packages globally, launch desktop applications,
  or read credentials.
- Nothing private, personal, compromising or confidential is committed, pushed or sent
  outside without the owner's prior approval; when in doubt, ask.
- Enforcement: `.agent-policy/guard.py` runs before the tool calls of every agent (from
  the Claude Code and Codex hook settings), the git hooks in `.githooks/` check commits
  and pushes, and `main` is protected on GitHub. Report a blocked action; never work
  around it.

## Commands

This section lists only commands that actually work. Frontend commands will be added by
issue #8. The full local validation gate is every check in this section.

One-time setup after cloning (enables the git hooks in `.githooks/`):

```sh
git config core.hooksPath .githooks
```

The hooks refuse to commit or push private files (internal notes, the local agent log,
scratch files, machine-local agent settings, `.env` files) and AI attribution trailers in
commit messages. The rules live in `.githooks/guards.sh`.

Agent policy tests (the shared guard, the git hooks, and Codex's hook command and rules).
Run them from the repository root, in Git Bash on Windows. They work on temporary copies and
never change the real agent settings; the Codex parts report SKIP without Windows or without
the Codex CLI of the VS Code extension:

```sh
python tests/policy/run_all.py
```

Audit script tests (issue #2). They run the script as a
subprocess and use no network beyond a local server on 127.0.0.1:

```sh
uv run --python 3.12 --no-project --with pytest==8.4.2 --with geographiclib==2.0 pytest tests/audit -q
```

CI runs these two suites on every pull request and push to `main`
(`.github/workflows/repo-tests.yml`, jobs `audit` and `policy`, Linux): guard cases that only
apply to Windows report SKIP with their reason, and the Codex parts report SKIP.

Backend quality gate (issue #3). Run from `backend/`; CI runs the same commands on every
pull request (`.github/workflows/backend.yml`):

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

Run the API locally from `backend/` (OpenAPI docs at http://127.0.0.1:8000/docs). Settings
come from environment variables or a local `.env`, documented in `backend/.env.example`:

```sh
uv run uvicorn citypulse.main:create_app --factory
```

## Data contract (non-negotiable)

- **Missing is not zero.** A station without a valid status has `null` availability, never `0`.
- A station that is not installed, not renting or not returning is **out of service**.
  It is not a bike shortage.
- Two separate risks: **few bikes to start a trip** and **few docks to return a bike**.
  Never merge them into a single "critical station" flag.
- Store timestamps in UTC and display them in `America/Toronto`. Keep source times
  (`last_reported`, feed `last_updated`) separate from our fetch time (`collected_at`).
  Any future forecast carries its own `forecast_issued_at`.
- Never fabricate history or forecasts. Demo or fixture data is labelled as such in the UI.
- Resolve GBFS feed URLs from the discovery file at request time. Do not hardcode feed paths.
- Keep the raw payload (with SHA-256 and fetch time) before normalising. Log every coercion
  as a quality flag. Keep unknown source fields.
- Coordinates are WGS84. Compute distances in metres (geodesic or a projected CRS),
  never from raw degree differences.
- Every displayed layer shows its source attribution and its `data_as_of` time.
- Data rules are written in `docs/data-quality.md` (issue #2). Each rule has a test.

## Quality bar

- Tests cover every acceptance criterion and every listed edge case.
- Strict type checking: Pyright strict and TypeScript strict.
- Report a change as passing only after the full validation gate, never after a partial check.
- No secrets in the repository. Configuration comes from environment variables,
  documented in a committed `.env.example`.
- Zero additional cost: no paid service and no mandatory LLM API.

## Commits

- Conventional Commits: `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`, `ci:`.
- The body explains what changed and why, and references the issue (`Closes #n` or `Refs #n`).
- No AI attribution trailers.

## Data sources and rights

- BIXI GBFS feed: CC BY 4.0, credit "BIXI Montréal" (licence checked on 2026-10-20 in the
  Montréal open-data catalogue). The data licence does not cover BIXI trademarks or logos;
  do not use the BIXI logo.
- Basemaps: OpenStreetMap and CARTO, each with visible attribution. Respect the OSM tile
  usage policy.
- Detailed desk research on candidate sources: `docs/data-source-audit.md`. Claims in that
  audit that were not checked against real files are hypotheses, not facts.

## Review checklist (reviewer agent)

- Does the diff satisfy every acceptance criterion of the linked issue?
- Does it violate the data contract above?
- Are edge cases tested? Do any tests pass trivially?
- Security: secrets, injection, unsafe deserialisation, unpinned third-party actions.
- Is there unnecessary complexity or abstraction?

Classify each finding as `blocker`, `should-fix` or `nit`.
