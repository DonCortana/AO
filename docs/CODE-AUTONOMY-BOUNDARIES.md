# Claude Code Autonomy Boundaries — Atlas Optimisation

**Read this at the start of every session in ~/projects/AO before doing anything else.**

**Version:** 1.2
**Origin:** v1.0 drafted 2026-09-01 after commit `f066c50` (Phase A) swept
two untracked, unreviewed, pre-existing files into a commit via `git add -A`.
v1.1 incorporated Claude Code's review of v1.0 (main-commit scope, confirm
semantics, mtime fragility, destructive-command gap). v1.2 incorporates
Code's review of v1.1: the live/ephemeral tell in v1.1 was factually wrong
in the dangerous direction (named an env var, `SUPABASE_DB_URL`, that
appears nowhere in the codebase except the doc itself); the destructive-
command list contained a self-contradiction (a judgment-call parenthetical
under a "no exceptions" heading, describing `git stash` behaviour
incorrectly); v1.1 silently dropped v1.0's pushed-history bullet; and
"unbypassable" overstated what a `settings.local.json` deny list — which
is itself gitignored and doesn't travel with the repo — can actually
guarantee.

This is a standing instruction, not an enforced permission system. See
"Enforcement" for what the one real permission layer can and cannot
guarantee.

---

## The core distinction

- **On a feature branch, reversible, nothing live touched** → proceed
  without asking. Explore, draft, write tests, iterate, commit to that
  branch.
- **Touches `main` in any way, touches the live database, or touches
  version/decision history** → stop, surface what you're about to do,
  wait for explicit confirmation.

"Touches `main` in any way" means commit, merge, push, or rebase — not
merge-or-push only. A direct `git commit` while checked out on `main` is
bucket two. If a task starts on `main`, create and switch to a feature
branch first — that's always allowed without asking, and is the default
first step of any task, not something that needs to be requested.

**Confirmation is per action, not per session.** If the person says "yes,
commit that to main," that authorizes the one commit under discussion, not
every subsequent bucket-two action for the rest of the session. Ask again
each time.

When unsure which bucket something is in, treat it as the second.

---

## Always allowed without asking

- Reading any file in the repo.
- Creating or switching to a feature branch.
- Running tests; running read-only linters (`ruff check`).
- Running migrations or queries against the ephemeral/test Postgres (see
  "Live vs. ephemeral" below for the actual tell).
- Working in a feature branch: writing code, writing tests, committing to
  that branch, iterating.
- Research: reading docs, fetching provider API references, drafting
  design notes as files in the branch.
- Reporting what you found, including problems, even ones outside the
  current task's scope — surface them, don't silently fix or silently
  ignore them.

## Always requires a stop-and-confirm, no exceptions

- **Any commit, merge, push, or rebase touching `main`.**
- **Rewriting already-pushed history**: `git push --force` / `-f` (on any
  branch, not just `main`), amending a commit that's already been pushed,
  `rebase` on `main`. (Restored from v1.0 — v1.1 dropped this bullet by
  accident when it restructured the main-commit rule.)
- **Any blanket staging or destructive command.** Named, not
  paraphrased, because the whole point is no ambiguity at the moment of
  typing the command: `git add -A`, `git add .` (and `-u`,
  `--all`, `--include-untracked` variants), `git commit -a` / `-am` (with
  or without `-m`), `git clean -fd`, `git checkout -- .`, `git restore .`,
  `git restore --staged .`, `git reset --hard`, `git stash -u` / `-a` /
  `--include-untracked` / `--all` (the untracked-sweeping forms
  specifically — plain `git stash` only touches tracked changes and is not
  in this list). Stage only an explicit, named list of the paths you
  touched this session. This applies even when the person's own
  instruction says "commit this" or "commit everything" — their go-ahead
  satisfies the *checkpoint*, it does not authorize a blanket form in
  place of a named list. Show the list either way.
- Any write to the live Supabase project — DDL, DML, RPC calls, storage
  writes.
- Any new or edited entry in `docs/decision-register.md`. Draft candidate
  text in a working file (`docs/draft-decisions-pending.md` is the
  existing pattern) and hand it back for review. Never commit a register
  entry directly, including "obviously correct" ones.
- Editing or reinterpreting an existing **Active** decision-register entry.
  Propose Superseded status instead of silently working around it.
- Incorporating an untracked file you did not create yourself earlier in
  this same session into the current diff — staging it, committing it,
  running a fixer/formatter on it, or building on its logic. Flag it by
  path and what it appears to do; let it be reviewed as its own item.
  (The test is "did I create this file in this session" — a fact you
  always have — not file mtime, which a fresh clone or checkout can
  rewrite, silently disarming an mtime-based rule.)
- Anything with a material cost implication — bulk provider API calls,
  anything that would show up materially on the budget rail.

## Live vs. ephemeral Postgres — the actual tell

Don't guess from context, and don't use `SUPABASE_DB_URL` — it does not
exist anywhere in this codebase; citing it was a v1.1 error. The real
selector, per Code's own audit of `src/atlas/config.py:60`:

- **Live**: anything reached through `get_settings()`. `_require()` raises
  if `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` are absent, so that code
  path cannot execute against anything other than the live project by
  construction. If a command or script imports and uses `get_settings()`
  (or the client it produces), it is live.
- **Ephemeral/test**: `ATLAS_TEST_DATABASE_URL` (the variable
  `tests/test_claim_task_postgres.py` and the rest of the Postgres-backed
  test suite actually use).

If a command's target isn't traceable to one of those two by inspection,
treat it as live and stop.

## Judgment calls — lean toward surfacing, not proceeding

- A fix that's clearly correct but wasn't asked for and touches a file
  outside the current task's stated scope: mention it, propose it as a
  separate follow-up, don't fold it into the current diff. (This overrides
  Code's own default of finishing a task including adjacent fixes found
  along the way — flagged explicitly because that default is a genuinely
  useful habit elsewhere, being deliberately narrowed here for smaller,
  more reviewable diffs.)
- `ruff --fix` or any other auto-fixer touching a file outside the current
  task's scope: same as above.
- Any place where "the spec/this doc didn't say" and you're inferring
  intent rather than following a stated instruction: name the gap rather
  than resolving it silently. (The v1.0→v1.1→v1.2 revisions are instances
  of exactly this — naming gaps instead of picking an interpretation is
  the intended behaviour, not an exception to it.)
- Whether an edit to *this file* is itself a bucket-two action: yes.
  Propose changes, don't self-edit.

## Subagents and worktrees

A subagent starts cold and has not read this file. If a task will spawn a
subagent or use a separate worktree, carry this document's rules into that
subagent's prompt explicitly — don't assume inheritance.

## Enforcement

**What it is:** a deny list on the blanket git forms above, held as Bash
tool permission rules. **What it can't be:** `.claude/settings.local.json`
is gitignored globally on this machine (`~/.config/git/ignore`) — a deny
list placed there is real and active in the current session, but it is
invisible to `git status`, cannot be code-reviewed as a diff, does not
travel to another clone or to CI, and does not survive a fresh checkout.
That is the same failure shape v1.1 just fixed for file-mtime detection,
reintroduced one layer down. The versioned, shareable target is
`.claude/settings.json` (tracked by convention in this repo; does not yet
exist) — the deny rules belong there, not in `.local.json`, once this
version is accepted.

**What the deny list actually guarantees:** it blocks the reflexive,
literal form of each command — the thing that produced the origin
incident. It is a prefix match on the command string; `git add -A -- .`,
`git -C . add -A`, the same command inside a script or `bash -c`, or any
other reformulation will not match. Call it "blocks the reflexive form,"
not "unbypassable" — the latter claims a guarantee this mechanism cannot
make and invites exactly the misplaced trust that produced `f066c50` in
the first place. Everything else in this document remains instruction the
model follows because it's stated, not because something prevents
otherwise — which is a real limit, not a formality.

---

## Practical effect

Prep, research, drafting, and iteration can happen unattended on a feature
branch across a full working session. The things that are hard to undo —
`main`, the live database, the register, prior decisions, and any code
whose origin isn't this session — always get a human checkpoint first,
regardless of how routine or obviously-correct the change seems.
"Obviously correct" is what the swept-in files looked like too, until
nobody had actually reviewed them.
