# Contributing

## Setup

```bash
nvm use            # Node 22 (20.19+ works)
npm install        # installs git hooks via husky
cp .env.example .env.local
```

## Workflow

1. Branch from `main`: `feat/...`, `fix/...`, `chore/...`.
2. Make the change with tests.
3. Run `npm run validate`. It is what CI runs.
4. Open a PR describing what changed, why, and how you checked it.

Pre-commit runs ESLint and Prettier on staged files, and the commit-msg hook enforces Conventional Commits.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org): `type(scope): summary`. Types are `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `build`, `ci`, `chore`, `revert`.

Mark breaking changes with `feat(api)!: ...` or a `BREAKING CHANGE:` footer. Add user-visible changes to `CHANGELOG.md` under "Unreleased".

## Code conventions

- TypeScript is strict. Don't use `any`; narrow the type. Indexed access can be `undefined` (`noUncheckedIndexedAccess`).
- Server data goes through TanStack Query hooks in `src/hooks`. Build keys with `queryKeys` (`src/lib/api/query-keys.ts`), not inline arrays.
- The browser never calls the backend directly. Everything goes through `/api/*`. Server-only code lives in `src/lib/server` and imports `server-only`.
- Report errors with `reportError` (`src/lib/monitoring.ts`). Lint forbids `console.log`.
- New user-facing strings go in `messages/en.json` and are read with `useTranslations` / `getTranslations`.
- Accessibility matters: use real buttons and links, label every control, trap and restore focus in overlays, respect reduced motion. jsx-a11y runs at "recommended" and e2e runs axe in both themes. If you disable a rule, say why in a comment.
- UI primitives are in `src/components/ui`. Run `npm run dev` and open `/dev/ui` to see them.
- Colours come from the tokens in `globals.css`. Keep text at 4.5:1 contrast or better.

## Testing

- Unit tests (Vitest, `src/**/*.test.ts`): pure logic such as permissions, guards and merge helpers.
- Hook and component tests (Testing Library + MSW, `src/**/*.test.tsx`): data hooks, components, optimistic updates.
- Route handler tests (`src/app/api/**/route.test.ts`): proxy behaviour. Put `// @vitest-environment node` at the top.
- End to end (Playwright, `e2e/*.spec.ts`): whole flows and accessibility.

Mock the network with MSW (`server.use(http.get(...))`) instead of stubbing `fetch`. Unhandled requests fail the test on purpose. E2E runs against `e2e/mock-backend.ts`; extend it when a flow needs a new endpoint.

## Adding an endpoint or screen

1. Add the type to `src/lib/api/types.ts`, or regenerate with `npm run gen:api`.
2. Add a hook in `src/hooks` with a key in `queryKeys`.
3. For a new top-level API area, add it to `ALLOWED_AREAS` in `src/lib/server/config.ts`.
4. Add loading and error states (`loading.tsx`, the shared `ErrorState`).
5. Test the hook and the main interaction. Add an e2e case if it's a primary flow.

## Decisions

Record significant decisions in `docs/adr`. Add a numbered ADR when you change security, data flow or a core dependency.

## Version decisions

- **TypeScript stays on 5.x.** TypeScript 7 exists, but the latest `typescript-eslint` (8.72.2, used through `eslint-config-next`) declares `typescript >=4.8.4 <6.1.0`. Upgrading would break linting. Revisit when typescript-eslint widens that range.
- **ESLint stays on 10.** `npm audit` reports moderate findings in the ESLint and typescript-eslint chain (dev dependencies only; production audit is clean). The suggested `npm audit fix --force` would downgrade ESLint and `eslint-config-next`, so it is not applied.
- **Tailwind CSS 4 is wired through `@tailwindcss/turbopack`** (published by Tailwind Labs, same version as `tailwindcss`). There is no PostCSS config.
- **Node 22** is what `.nvmrc`, CI and the Dockerfile use. `engines` allows 20.19+.

## License and ownership

CeDesk is released under the [MIT License](LICENSE). By opening a pull request you agree that your contribution is licensed under the same MIT License (inbound = outbound). You keep the copyright in your work.

- There is **no Contributor License Agreement** and no sign-off requirement. Open a pull request and we will review it.
- Only submit code you wrote or have the right to submit under MIT. Do not copy in code from GPL, AGPL, LGPL or other copyleft projects, or from sources whose license you have not checked.
- New dependencies must use a license compatible with MIT distribution (MIT, BSD, ISC, Apache-2.0 and similar). Mention the license in the pull request.
