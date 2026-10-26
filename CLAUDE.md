# CLAUDE.md — CancerResearch

## Project rules come first

`OncoScope_MVP/AGENTS.md` and the documents it lists (`README.md`, `docs/ARCHITECTURE.md`, `docs/SECURITY.md`,
`docs/ROADMAP.md`) remain binding. Read them before changing OncoScope code. In particular: synthetic data only, local-only
(loopback) services, no external AI with record data, no diagnosis/staging/response classification/treatment
recommendations, every update audited, and no real patient records before the M3 privacy/security review passes. Nothing
in this file relaxes those constraints.

## Git workflow (permanent, for all development tasks)

1. **Branch.** Work on a dedicated feature branch created from the latest appropriate base: normally a fresh
   `origin/main` (`git fetch origin` first). Branch from an unmerged feature branch only when the task depends on it, and
   say so in the PR. Name branches `<type>/<short-kebab-description>`, e.g. `feat/ca125-extraction`, `fix/anchor-offsets`.
2. **Implement and test.** Complete the change and run the relevant tests before committing:
   - backend: `cd OncoScope_MVP/backend; python -m pytest -q`
   - frontend: `cd OncoScope_MVP/frontend; npm run build; npm test`
   - browser (UI or workflow changes): `npm run test:e2e`
3. **Commit** with [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): summary` with types such as
   `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `build`, `ci`. Commit each coherent milestone separately; explain
   the why in the body.
4. **Push** the completed feature branch to `origin` automatically (`git push -u origin <branch>`).
5. **Open or update the pull request** targeting `main` automatically: create one with `gh pr create --base main` if none
   exists for the branch; otherwise push to the same branch and update the existing PR's description when the scope changes.
6. **No routine confirmation.** Do not ask before pushing a feature branch or opening/updating its PR when existing
   permissions authorize those actions.
7. **Report** the PR link, the test commands run with their results, and any known limitations or unverified items.

### Never, without explicit authorization from the user

- force-push (`--force`, `--force-with-lease`) or rewrite published history
- push directly to `main`
- merge pull requests (or enable auto-merge)
- deploy publicly or expose the API/database beyond `127.0.0.1`
- commit patient data, real medical documents, local databases (`backend/data/`), documents, backups, or `.env` files
