## Development Workflow

### Step-by-Step Process

```
Step 1:  PLAN       → skill: everything-claude-code:plan          via Agent(model="opus")
Step 2:  TESTS      → skill: everything-claude-code:tdd            via Agent(model="sonnet")
Step 3:  IMPLEMENT  → skill: everything-claude-code:build-fix      via Agent(model="sonnet")
Step 4:  VERIFY     → skill: everything-claude-code:verify         via Agent(model="sonnet")
Step 5:  REVIEW     → skill: everything-claude-code:python-review  via Agent(model="opus")
Step 6:  FIX PLAN   → skill: everything-claude-code:plan           via Agent(model="opus")   (for review issues)
Step 7:  FIX        → skill: everything-claude-code:build-fix      via Agent(model="sonnet")  (after user approval)
Step 8:  RE-VERIFY  → skill: everything-claude-code:verify         via Agent(model="sonnet")
Step 9:  COMPLETE   → skill: everything-claude-code:update-docs    via Agent(model="haiku")  (update todo.md + lessons.md)
Step 10: SAVE       → skill: everything-claude-code:save-session   via Agent(model="haiku")
```

**Model rationale:**
- `opus` — deep reasoning tasks: planning, architecture, code review
- `sonnet` — balanced tasks: test writing, verification
- `haiku` — fast execution tasks: implementation edits, docs, session save

**CRITICAL — model enforcement:**
- The `Skill` tool always executes inline in the current session model and does NOT switch models.
- To actually run a skill under a different model, wrap it in a `general-purpose` Agent with the `model` parameter set:
  ```
  Agent(subagent_type="general-purpose", model="opus",   description="...", prompt="Use the Skill tool: skill='everything-claude-code:plan', args='...'")
  Agent(subagent_type="general-purpose", model="haiku",  description="...", prompt="Use the Skill tool: skill='everything-claude-code:build-fix', args='...'")
  Agent(subagent_type="general-purpose", model="sonnet", description="...", prompt="Use the Skill tool: skill='everything-claude-code:verify', args='...'")
  ```
- `general-purpose` agents have all tools (including `Skill`), so skills invoked this way work correctly.
- Never call `Skill` directly from the main session when a non-Sonnet model is required.

**At each step:**
1. At the start of a new session or step, spawn `Agent(model="haiku")` to invoke `everything-claude-code:resume-session`
2. Complete the step **by spawning an Agent with the correct model that invokes the designated skill — do not call Skill directly from the main session**
3. Present results to user
4. Spawn `Agent(model="haiku")` to invoke `everything-claude-code:save-session`
5. **Ask: "Ready to proceed to [next step]?"**
6. Wait for user confirmation before moving on

**CRITICAL: Every step must use its designated skill.** Running `pytest` manually is not a substitute for `everything-claude-code:verify`. Writing tests inline is not a substitute for `everything-claude-code:tdd`. Writing a plan in conversation is not a substitute for `everything-claude-code:plan`. The skills enforce consistency, capture output, and provide structured guidance that ad-hoc commands do not.

**Use `/everything-claude-code:docs` anytime** you need library/framework documentation (PyPika, FastAPI, Pydantic, Snowflake SQL, etc.) — don't guess.

**Session file retention:** Keep at most 3 session files in `.claude/sessions/`. After saving a new session, delete the oldest files if count exceeds 3. Retention is count-based, not time-based.

---

## Workflow Orchestration

### 1. Plan Mode Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop
- After ANY correction from the user: update `.claude/tasks/lessons.md` under "Coding Lessons"
- When the user answers a question about the project: update `.claude/tasks/lessons.md` under the domain knowledge section
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops

### 4. Code Review → Plan → Fix (Never Skip Planning)
- After code review finds issues, **DO NOT jump straight to fixing**
- Use `/everything-claude-code:plan` to create a fix plan
- Update the relevant plan file with the proposed fixes
- Ask clarifying questions if any issue involves a design decision
- Wait for user approval before implementing
- This applies to both feature implementation and code review fixes

### 5. Plan Changes Must Be Written to the Plan File
- **ANY change to a plan** — new phases, revised column definitions, architectural decisions, fix plans — must be written to the relevant `.claude/plans/*.md` file **before** implementing anything
- This is a hard prerequisite: no code changes until the plan file reflects the approved plan
- The plan file is the source of truth; verbal plans or in-conversation summaries are not sufficient
- After writing the plan file, present it to the user and wait for confirmation

### 6. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 7. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes don't over-engineer
- Challenge your own work before presenting it

### 8. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management
1. **Plan First**: Write complete plan to relevant `.claude/plans/*.md` file and also add or update `.claude/tasks/todo.md` with checkable items to track progress.
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go — **update `.claude/tasks/todo.md` immediately after completing any task, not later**
4. **Explain Changes**: High-level summary at each step
5. **Document Results**: Add review section to `.claude/tasks/todo.md`
6. **Capture Lessons**: Update `.claude/tasks/lessons.md` after corrections

## Knowledge Base
**Before planning, building, or fixing anything, read `.claude/tasks/lessons.md`.**

This single file contains:
- **Domain knowledge**: table schemas, UI behavior, API design decisions, filter logic
- **Coding lessons**: mistakes from past phases with rules to prevent them

Update it whenever:
- The user answers a question about the project (domain knowledge)
- A code review catches an issue (coding lesson)
- A correction is made (new rule)

## Core Principles
- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what's necessary. Avoid introducing bugs.

## Cross-Project Lessons

Pin these into every new project's `.claude/tasks/lessons.md` on day one. They came from real bugs and corrections; treat them as defaults, not suggestions.

**Environment**
- Use the project toolchain path explicitly (e.g. `.venv/bin/pytest`, `./node_modules/.bin/...`). Bare commands resolve via system PATH and silently use the wrong site-packages.
- Paste the full toolchain path into every subagent prompt. Spawns cold-start and do not inherit shell context.

**Config & Errors**
- Validate config at load time with field-named errors. Malformed input raises loud — never silently default.
- Apply a size-budget cap (~1MB typical) with a field-named error on every file input: config, fixtures, lockfiles, datasets.

**Secrets**
- Redact secrets in `str(exc)` **before** branching on exception type. Subclass exceptions (e.g. `HTTPError` ⊂ `URLError`) can silently turn a safe branch into a leak.
- Never send raw user data or logs to LLM prompts. Pass scoped evidence only.

**APIs & Architecture**
- Keep external services (cloud SDKs, third-party APIs) behind a Protocol/interface boundary. Local tests must not require real credentials.
- Use Protocol/interface typing on orchestrator collaborators — never `Any`. Preserves the contract under refactor and static analysis.
- Documented-but-dead parameters violate YAGNI. Remove them; do not leave them as placeholders.
- Metric and field names must be semantically honest (e.g. `_upper_bound_seconds` not `_seconds` when the value is an upper bound).
- When parallel/overlapping APIs exist, document semantic divergence and any defense the new API cannot tune **immediately**.

**Runtime Hygiene**
- Validate config-vs-mode invariants in `__init__`, not per-iteration. One clear startup error beats N redacted runtime errors.
- When bridging API A → API B, default-preserve: forward only what A explicitly exposes; let B's defaults stand for the rest.
- `fcntl`/`flock` must target a stable named path (`<resource>.lock`), not a per-call temp file. Verify mutual exclusion with a real concurrent holder in tests — never trust the `flock` call alone.
- Partitioned sink writes need a per-write unique suffix (e.g. UUID). Reusing a partition path silently overwrites prior data.

**Workflow**
- Work on a feature/dev branch, never `main`.
- Implementation wins as ground truth on doc/code drift — update the plan to match the code, unless the code name is genuinely misleading.
- Treat existing tests as locked public contracts. Extend, do not rewrite.

## Permissions
- You have permission to run any test runner bash commands needed by this project (e.g. `pytest`, `go test`, `npm test`).