# Contributing to CeDesk

Thanks for helping. Bug reports, fixes, docs and features are all welcome.

## Licence of your contribution

CeDesk is MIT licensed ([LICENSE](LICENSE)). By opening a pull request you agree that
your contribution is licensed under the same MIT licence. There is no Contributor
License Agreement to sign.

Only send code you wrote yourself or have the right to share under MIT. Do not copy in
code under GPL, AGPL or another licence that is not MIT-compatible, and say in the pull
request where any third-party code came from.

## Report a bug or ask for a feature

Open an issue with what you did, what you expected and what happened instead (steps,
logs, CeDesk version or commit). Security problems go through [SECURITY.md](SECURITY.md),
never a public issue.

## Set up

The repository has two parts:

- **Backend** (this folder): Python 3.12 via [uv](https://docs.astral.sh/uv/) and Docker.
  The steps are in [Run it locally](README.md#run-it-locally).
- **Web app** ([frontend/](frontend)): Node 22. Its setup, conventions and checks are in
  [frontend/CONTRIBUTING.md](frontend/CONTRIBUTING.md).

## Make a change

1. Fork the repository and create a branch from `main` (`fix/...`, `feat/...`, `docs/...`).
2. Keep the [rules every change keeps](README.md#rules-every-change-keeps): tenancy,
   roles, layers, one migration head and lock order.
3. Add or update tests for what you change. A database change needs a migration in
   `migrations/versions/`.
4. After an API change, run `uv run cedesk openapi` and commit `docs/api/openapi.json`.
5. Run the same checks as CI before you push (for the web app: `npm run validate` in
   `frontend/`):

   ```bash
   uv run ruff check . && uv run ruff format --check .
   uv run mypy
   uv run lint-imports
   uv run pytest
   ```

## Open a pull request

- One change per pull request, with a short description of what and why.
- Commit messages follow `type(scope): summary`, for example
  `fix(reports): scheduled reports keep to a time budget`.
- CI must pass and a maintainer must approve before it is merged into `main`.

## Code of conduct

See the [Code of Conduct](CODE_OF_CONDUCT.md).
