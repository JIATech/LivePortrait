# Skill Registry

**Delegator use only.** Any agent that launches sub-agents reads this registry to resolve compact rules, then injects them directly into sub-agent prompts. Sub-agents do NOT read this registry or individual SKILL.md files.

See `_shared/skill-resolver.md` for the full resolution protocol.

## User Skills

| Trigger | Skill | Path |
|---------|-------|------|
| When creating a GitHub issue, reporting a bug, or requesting a feature. | issue-creation | `C:\Users\j.arnaboldi.spb\.config\opencode\skills\issue-creation\SKILL.md` |
| When creating a pull request, opening a PR, or preparing changes for review. | branch-pr | `C:\Users\j.arnaboldi.spb\.config\opencode\skills\branch-pr\SKILL.md` |
| When user asks to create a new skill, add agent instructions, or document patterns for AI. | skill-creator | `C:\Users\j.arnaboldi.spb\.config\opencode\skills\skill-creator\SKILL.md` |
| When writing Go tests, using teatest, or adding test coverage. | go-testing | `C:\Users\j.arnaboldi.spb\.config\opencode\skills\go-testing\SKILL.md` |
| When user says "judgment day", "judgment-day", "review adversarial", "dual review", "doble review", "juzgar", "que lo juzguen". | judgment-day | `C:\Users\j.arnaboldi.spb\.config\opencode\skills\judgment-day\SKILL.md` |

## Compact Rules

Pre-digested rules per skill. Delegators copy matching blocks into sub-agent prompts as `## Project Standards (auto-resolved)`.

### issue-creation
- Search existing issues before creating a new one.
- Use the GitHub issue template; blank issues are not allowed.
- Questions belong in Discussions, not Issues.
- New issues always start with `status:needs-review`.
- A maintainer must add `status:approved` before any PR may be opened.
- Fill all required template fields, including pre-flight checks.

### branch-pr
- Every PR must link an approved issue with `Closes/Fixes/Resolves #N`.
- Branch names must match `type/description` using lowercase `a-z0-9._-`.
- Use conventional commits only; never add `Co-Authored-By` trailers.
- PR must have exactly one `type:*` label.
- Use the PR template with summary, changes table, and test plan.
- Automated checks must pass before merge; blank PRs without issue linkage are blocked.

### skill-creator
- Create a skill only for reusable, non-trivial AI workflows or conventions.
- Use `skills/{skill-name}/SKILL.md` with complete frontmatter and Trigger text.
- Prefer actionable critical patterns over long explanations.
- Keep examples minimal and focused; use local references, not web URLs.
- Put templates/schemas in `assets/` and documentation pointers in `references/`.
- Register each new skill in `AGENTS.md`.

### go-testing
- Prefer table-driven tests for multi-case logic.
- Test Bubbletea state transitions directly through `Model.Update()`.
- Use `teatest.NewTestModel()` for interactive TUI flows.
- Use golden files for stable view/output assertions.
- Mock side effects and use `t.TempDir()` for filesystem work.
- Cover both success and error paths explicitly.

### judgment-day
- Resolve matching compact rules from the registry before launching judges.
- Launch exactly two blind judges in parallel; neither sees the other.
- Judges return findings only, classified as `CRITICAL`, `WARNING (real)`, `WARNING (theoretical)`, or `SUGGESTION`.
- Only confirmed CRITICALs and real WARNINGs block approval.
- Fix only confirmed issues, then re-judge in parallel.
- After two fix iterations, escalate to the user before continuing.

## Project Conventions

| File | Path | Notes |
|------|------|-------|
| — | — | No project-level convention files detected in the workspace root. |

Read the convention files listed above for project-specific patterns and rules. All referenced paths have been extracted — no need to read index files to discover more.
