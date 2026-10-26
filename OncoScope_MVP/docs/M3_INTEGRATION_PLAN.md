# Integration plan for open pull requests (as of 2026-10-26)

Nothing below has been merged. Merging requires the repository owner's authorization.

## Open pull requests

| PR | Branch | Base | Commits beyond `main` | Content |
|---|---|---|---|---|
| #1 | `feat/m2.5-evidence-reconstruction` | `main` | 8 | M2.5 evidence anchors, extraction, review queue, findings, backup/restore |
| #2 | `chore/claude-git-workflow` | `main` | 1 | `CLAUDE.md` (repository Git workflow) only |
| #3 | `feat/m2.6-document-fidelity` | `main` | 18 = the 8 commits of #1 (same SHAs) + 10 M2.6 commits | pdf.js viewer, benchmark, parser v2.1, history view |
| (new) | `feat/m3.0-secure-foundations` | `main` | #3's 18 + M3.0 commits | CI, inventory, threat model, security tests/fixes, M3.1 plan |

`feat/m2.5…` is an ancestor of `feat/m2.6…`, which is an ancestor of `feat/m3.0…`: the commits are shared, not duplicated
copies, so merging in order produces no duplicate history. `main` contains only the initial commit.

## Conflict check

Trial merges in a throwaway worktree (git 2.37; nothing committed or pushed), starting from `origin/main`:
`#1` → fast-forward, 0 conflicts; then `#3` → fast-forward, 0 conflicts; then `#2` → merge commit, 0 conflicts
(it only adds `CLAUDE.md`, which no other branch touches). `#2` can also go first with no effect on the others.

## Recommended order

1. **#2** (`CLAUDE.md`) — independent, documentation only; merging first makes the workflow binding for later sessions.
2. **#1** (M2.5) — fast-forward.
3. **#3** (M2.6) — becomes a fast-forward once #1 is in; its diff then shows only the 10 M2.6 commits.
4. **M3.0 PR** — its diff then shows only the M3.0 commits. CI (added in M3.0) starts running on PRs to `main` once
   the workflow file is on the PR branch; #1–#3 predate it and are covered by the M3.0 run, which contains them.

Use "Rebase and merge" or "Create a merge commit" for #1 and #3, **not "Squash and merge"**: squashing would give
`main` new commit IDs, after which #3 and the M3.0 PR would show the M2.5 changes again as conflicts/duplicates and would
need a rebase. If a squash is preferred anyway, rebase each dependent branch onto the new `main` with `--onto` (a
force-push of the feature branch, which the workflow allows only with explicit authorization).

Before each merge: CI green on the M3.0 PR (it exercises the combined code), and the PR's own test plan checked.

## M3.1-A (added after M3.0)

The M3.1-A branch `feat/m3.1a-protected-access` was cut from `feat/m3.0-secure-foundations` at `b3975aa` because
#1–#4 are still unmerged; `main` lacks every later commit. Its PR targets `main` and therefore also shows the commits of
#1, #3 and #4 until those are merged. Merge it **after #4**, in the same order as above (#2, #1, #3, #4, then M3.1-A);
it then shows only its own commits. Same rule: no squash-merging of #1, #3 or #4.
