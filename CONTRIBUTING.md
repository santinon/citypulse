# Contributing

## Prerequisites

- Git
- [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed by uv when needed)
- Node.js 24 LTS and npm
- Docker Desktop (or Docker Engine) with Compose v2

## How work flows

1. Pick an open issue, assign yourself, and read its acceptance criteria.
2. Branch from `main`: `<issue-number>-<short-slug>`.
3. Make the change with tests. Run the full validation gate listed in `AGENTS.md`.
4. Open a pull request that closes the issue and complete the template checklist.
5. A reviewer (human or agent) reviews the diff; address the findings once.
6. When CI is green and a maintainer approves, the PR is squash-merged into `main`.

`AGENTS.md` holds the shared rules: language, data contract, quality bar and commit style.
They apply to humans and coding agents alike.
