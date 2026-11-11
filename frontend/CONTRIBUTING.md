# Contributing

## Setup

```bash
nvm use            # Node 22 (20.19+ works)
npm install        # installs git hooks via husky
cp .env.example .env.local
```

## Workflow

1. Branch from `main`: `feat/…`, `fix/…`, `chore/…`.
2. Make the change **with tests** (see below).
3. `npm run validate` must pass. It is exactly what CI runs.
4. Open a PR; describe what changed and why, and how you verified it.

Pre-commit runs ESLint and Prettier on staged files; the commit-msg hook enforces Conventional Commits.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org): `type(scope): summary`

`feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `build`, `ci`, `chore`, `revert`.
Breaking change: `feat(api)!: …` or a `BREAKING CHANGE:` footer. Update `CHANGELOG.md` under "Unreleased".

## Code conventions

- **TypeScript strict.** No `any`; narrow instead. Indexed access can be `undefined` (`noUncheckedIndexedAccess`).
- **Server data goes through TanStack Query hooks** in `src/hooks`. Build keys with `queryKeys` (`src/lib/api/query-keys.ts`); never write key arrays inline.
- **Never call the backend directly from the browser.** Everything goes through `/api/*`. Server-only code lives in `src/lib/server` and imports `server-only`.
- **Errors**: report with `reportError` (`src/lib/monitoring.ts`); do not add `console.log`. Lint forbids it.
- **Text**: new user-facing strings go in `messages/en.json` and are read with `useTranslations` / `getTranslations`.
- **Accessibility is a requirement, not polish.** Use real buttons and links, label every control, trap and restore focus in overlays, respect reduced motion. jsx-a11y runs at "recommended" and e2e runs axe in both themes. If you must disable a rule, say why in the comment.
- **UI primitives** live in `src/components/ui`; see them all at `/dev/ui` while running `npm run dev`.
- **Colours** come from design tokens in `globals.css`; keep text at 4.5:1 or better.

## Testing

| Kind          | Tool                        | Where                          | Use for                                              |
| ------------- | --------------------------- | ------------------------------ | ---------------------------------------------------- |
| Unit          | Vitest                      | `src/**/*.test.ts`             | Pure logic: permissions, guards, patch/merge helpers |
| Hooks & views | Testing Library + MSW       | `src/**/*.test.tsx`            | Data hooks, components, optimistic updates           |
| Route handler | Vitest (`node` environment) | `src/app/api/**/route.test.ts` | Proxy behaviour; use `// @vitest-environment node`   |
| End to end    | Playwright                  | `e2e/*.spec.ts`                | Whole flows and accessibility                        |

Mock the network with MSW (`server.use(http.get(...))`) instead of stubbing `fetch`. Unhandled requests fail the test on purpose. E2E runs against `e2e/mock-backend.mjs`; extend it when a flow needs a new endpoint.

## Adding an endpoint or screen

1. Add the TypeScript type to `src/lib/api/types.ts` (or regenerate with `npm run gen:api`).
2. Add a hook in `src/hooks`, with a key in `queryKeys`.
3. For a new top-level API area, add it to `ALLOWED_AREAS` in `src/lib/server/config.ts`.
4. Add loading and error states (`loading.tsx` / the shared `ErrorState`).
5. Test the hook and the main interaction; add an e2e case if it is a primary flow.

## Decisions

Significant decisions are recorded in `docs/adr`. Add a numbered ADR when you change security, data flow or a core dependency.
