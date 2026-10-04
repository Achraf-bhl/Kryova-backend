# Kryova — the road to 10/10

*Written 2026-10-04, from a read of both repositories on that day. Every path below was checked to
exist when this was written; anything marked **(verify)** was inferred from code and has not been
run.*

**Where we stand: 6/10 overall.** The engineering is 8/10: verification is designed in, about 11,000
tests check results against closed forms, and guards are proved by breaking them. Product readiness
is 4/10: the hosted model has never answered a live request, the GUI ladder has rarely been driven
end to end, the desktop installer only works on the machine that built it, and the CATIA
integration is one-way with no live view.

**What 10/10 means here.** An engineer installs one signed file on a Windows workstation that has
CATIA. They describe a machine in plain language, watch it being built in CATIA and in Kryova at the
same time, see stress, fatigue and kinematic results on the geometry, and leave with drawings and a
technical file a licensed engineer can review. It costs a predictable, visible number of tokens, and
nothing in the chain claims more than it measured. The scorecard at the end makes that measurable.

---

## How to read this file

- **Paths.** `Kryova-backend/…` is `/home/siaziz/Desktop/Kryova-backend/…` on Linux and the same
  checkout on the Windows seat. `Kryova-frontend/…` is the sibling repository.
- **This file does not replace the master plan.** `Kryova-backend/KRYOVA_MASTER_PLAN.md` is still
  where status lives. Each item below either names the master-plan task it advances (`→ P7.1`) or
  is marked **NEW**. A NEW item is added to the plan as a task in the phase that owns it, in the same
  commit as its first line of code (CLAUDE.md, *Keeping the master plan current*, rule 6).
- **Size** is a rough guide: **S** = under a day, **M** = a few days, **L** = one to three weeks,
  **XL** = longer, or blocked on something outside the code.
- **Machine.** **[Linux]** can be done and tested on the Linux box. **[Seat]** needs the Windows
  machine with CATIA. **[Both]** means it is written on Linux and proved on the seat.
- **Every item obeys the standing rules**:
  - Tests ship with the code, and each new guard is broken on purpose to watch it fail.
  - `ruff`, `mypy` and `alembic check` stay clean.
  - Never trade the product for speed. `Kryova-backend/docs/MAKING_IT_FASTER.md` §4 forbids three
    things: silent mesh coarsening, skipping a convergence study, and sampling where the result
    claims to be measured.
  - Never claim a number nobody measured.

### Order of phases, and why

| Phase | Name | Depends on | Why this position |
|---|---|---|---|
| 0 | Measure the real thing first | — | Every later number needs a baseline that does not exist yet |
| 1 | Token economy | 0 | The model is the cost and the latency; nothing else comes close |
| 2 | Conversation logic: memory, summary, continue | 0, 1 | Long design sessions are the product |
| 3 | Rate limits and quotas | 1 | Spend has to be both bounded and visible |
| 4 | A desktop app that installs anywhere | — | The release blocker today; it runs in parallel with 0–3 |
| 5 | Kryova ↔ CATIA, both directions | 4 | The integration customers are buying |
| 6 | Performance and full use of the user's PC | 0 | Measured, so it comes after the baseline |
| 7 | Project management | — | Independent; can run in parallel |
| 8 | Interface | 1, 2, 5, 7 | Puts what the earlier phases built in front of the user |
| 9 | Reliability, security, operations | — | Runs in parallel throughout |
| 10 | Proof: the 10/10 gate | all | The only phase that can award the score |

---

## Phase 0 — Measure the real thing first

**Goal.** Replace every assumption about the hosted model with a measurement, and record the
baseline that every later phase is judged against. Nothing in Phases 1, 2 and 6 may claim an
improvement without a before-and-after taken from this baseline.

- [ ] **0.1 Run THE QUEUE section H against live DeepSeek (H1–H9).** [Seat or any machine with network] **M** → P11.1
  - Where: `Kryova-backend/docs/WINDOWS_VERIFICATION.md` §H (line ~1731),
    `Kryova-backend/app/ai/providers/deepseek.py`, `Kryova-backend/app/ai/providers/openai_compatible.py`.
  - What it settles:
    - Does `reasoning_content` echo survive the second step (H1, H2)?
    - JSON mode with and without thinking (H3, H4).
    - Image input (H5).
    - SSE tool-call assembly (H6).
    - The prompt cache hit rate (H7).
    - `AI_EFFORT_CHAT` low versus high (H8).
    - `AI_TOOL_LIMIT` 0 versus 60 (H9).
  - Done when: each H row is ticked with the transcript or log line that answers it, and any defect
    it finds is fixed with a test in `Kryova-backend/tests/test_deepseek_provider.py` or
    `test_openai_compatible_resilience.py`.
  - **Status 2026-10-04: BLOCKED on an API key, not on the network.** This Linux machine reaches the
    internet now (`api.deepseek.com` answered 401, `eur-lex.europa.eu` 202, GitHub 200), so the old
    "no outbound network from Linux" line no longer decides this. No `AI_API_KEY` is configured in
    `.env` or `.env.local` and none is in the environment, and a key is the user's to supply. With
    one, H1–H7 can be run from here: they need a chat, not a seat.
- [x] **0.2 Write a turn-metrics recorder.** [Linux] **M** — **NEW** (E15 or P11)
  - Today the numbers exist only as log lines (`agent step … prompt tokens` in
    `Kryova-backend/app/ai/agent.py`, `prompt cache: … hits` in
    `Kryova-backend/app/ai/providers/openai_compatible.py::_log_prompt_cache`).
  - Persist one row per turn: steps taken, prompt tokens, cached prompt tokens, completion tokens,
    wall time, stop reason, the number of tools offered, and the number of tool calls that failed.
  - Where: a new table beside `ai_token_usage` (`Kryova-backend/app/models/conversation.py`), plus a
    migration and `Kryova-backend/app/ai/usage.py`.
  - Test: a turn driven by a fake provider writes exactly one row, with the sums the fake reported.
- [ ] **0.3 Run the baseline GUI ladder: one prompt per level, Levels 1–6.** [Seat] **L** → Part 2 gates
  - Where: `Kryova-backend/docs/GUI_PROMPT_LADDER.md`; the report goes in
    `Kryova-backend/docs/verification-<date>/`.
  - Record for every level: the rung reached, the screenshots, and the 0.2 metrics.
  - Done when: a dated report exists with real numbers. This report **is** the baseline.
- [ ] **0.4 Time a cold start and a warm start of the desktop app.** [Seat] **S** — **NEW** (P7)
  - Measure from double-click to an interactive chat, using the logs that
    `Kryova-frontend/src-tauri/src/lib.rs` writes to `%LOCALAPPDATA%\Kryova\logs\`.
  - Record backend boot, Postgres start (`Kryova-backend/app/core/local_postgres.py`), Next start,
    and the first `/health` 200.

---

## Phase 1 — Token economy: limits, accounting and optimisation

**Goal.** Every token is counted at its true price, the user can see what a turn costs, and a
typical turn costs much less than today's baseline, with no loss of accuracy on the ladder.

### 1A. Count tokens at their true price

- [x] **1.1 Record cached prompt tokens in the ledger.** [Linux] **M** → P11.3
  - Problem: `TokenUsage` in `Kryova-backend/app/ai/provider.py` folds cached reads into
    `prompt_tokens`. DeepSeek reports `prompt_cache_hit_tokens`, but it is only logged
    (`openai_compatible.py::_log_prompt_cache`) and never stored. Anthropic's
    `cache_read_input_tokens` is summed into input (`Kryova-backend/app/ai/providers/anthropic.py`).
    So the daily budget charges a cached token as if it were fresh, while the vendor bills cached
    input at a fraction of the price.
  - Do: add `cached_prompt_tokens` to `TokenUsage`, keeping the sum backward-compatible; add a
    column on the ledger (migration); fill it from both providers.
  - Test: a fake usage block with hits and misses records both, and the budget reads the split.
- [x] **1.2 Budget by cost, not by raw tokens.** [Linux] **M** — **NEW** (P8)
  - Today `ai_daily_token_budget = 2_000_000` (`Kryova-backend/app/core/config.py:310`,
    `Kryova-backend/app/ai/usage.py::over_budget`) counts every token the same.
  - Do: add a per-model price table in config (input, cached input, output, per million tokens),
    compute a cost per call, and enforce a daily *cost* budget per user and per organisation.
  - Do not hard-code prices. They live in config, so moving from DeepSeek to OpenAI changes only
    `.env` (the user's rule).
  - Test: two calls with the same token count and a different cache share cost different amounts,
    and the budget trips on cost.
- [x] **1.3 Organisation-level budgets and alerts.** [Linux] **M** → P8
  - The ledger is per user (`usage.py`). Add an org daily and monthly cap, with soft warnings at 80%
    and 100% through `Kryova-backend/app/mail/` and an in-app banner.
  - **Status 2026-10-04:** done and tested on both sides (master plan P11.8, `app/ai/org_budget.py`;
    P11.9, `Kryova-frontend/src/components/ai-budget-banner.tsx`). A real organisation crossing 80 %
    in a browser is the next gate run's. `[~]` in this file means "done except for the piece named
    under the item".
- [x] **1.4 Stop losing usage on a failed decision.** [Linux] **S** — flagged 2026-10-04
  - `Kryova-backend/app/ai/decide.py::_ask`: when the provider raises `LLMError` after a paid repair
    attempt, the usage of the attempts already made is lost.
  - Carry it on the exception, or return it with the fallback `Decision`.
  - Test: a provider that answers badly and then raises still reports the first attempt's tokens.

### 1B. Send fewer tokens

- [ ] **1.5 Turn on tool retrieval once the ladder shows it is safe.** [Seat to measure, Linux to code] **M** → P11.3, H9
  - `AI_TOOL_LIMIT=0` (`config.py:281`) offers about 235 schemas, around 58k tokens, on every step.
    Retrieval at a limit of 40–80 was measured offline to cut 70–78% of the schema bytes
    (`Kryova-backend/app/ai/tool_retrieval.py`, `MIN_MATCH_SLOTS=8`, `DEFAULT_LIMIT=40`).
  - Do: run the Phase 0 ladder at 0 and at 60. Switch the default only if every level reaches the
    same rung or better.
  - Test: already pinned (`Kryova-backend/tests/test_tool_retrieval.py`). The decision is recorded
    with its ladder numbers.
  - **Status 2026-10-04: BLOCKED on 0.1 and 0.3** (a live key, then the ladder at 0 and at 60). There
    is no Linux code left to write: retrieval, its tests and the switch all exist. One thing the
    comparison must account for, found while building 1.13: **the design tools (`record_design`,
    `read_design`, `set_design_parameter`, `build_design`) are in no intent family and not in
    `CORE_TOOLS`**, so retrieval at 60 would withhold them, and the two arms would differ by more
    than the schema count (THE QUEUE H13 part c).
- [~] **1.6 Make the tool schemas themselves shorter.** [Linux] **M** — **NEW** (P11.3)
  - Measure the bytes per schema: `toolbox.schemas()` in `Kryova-backend/app/ai/tools.py` and the
    specs in `Kryova-backend/app/catia/tool_specs.py`. Rank the top 30 by size, cut repeated prose
    from the descriptions, and move long guidance into the KB (`Kryova-backend/app/catia_kb/`),
    which the agent can look up when it needs it.
  - Guard: `Kryova-backend/tests/test_prompt_cache_stability.py` must stay green. Add a test that
    caps the total schema bytes at the new measured value, so the registry cannot quietly grow back.
  - **Status 2026-10-04:** measured and capped, not shrunk (master plan P11.10,
    `scripts/schema_report.py`, `tests/test_tool_registry_size.py`): 244 tools, 239.6 KB, ~66.5k
    tokens estimated. The trim waits for 1.5's accuracy ladder, because cutting prose changes what
    the model is offered.
- [x] **1.7 Shrink old tool results in the replay.** [Linux] **M** — **NEW** (E16) → P11.11
  - Every tool result is fenced at `MAX_TOOL_RESULT_CHARS = 6_000`
    (`Kryova-backend/app/ai/sanitise.py:49`) and replayed whole while it sits in the window
    (`Kryova-backend/app/ai/context.py::_replay`, window `ai_max_context_messages=40`).
  - Do: keep the last *k* tool results verbatim and replace older ones with a one-line digest:
    tool, ok/error, and the key numbers. The full text stays in the database, and the agent can
    re-read it with a tool when it needs to.
  - Trap: the digest must be deterministic and byte-stable, or it breaks the cache prefix. Build it
    from the stored row only, never with a model.
  - Test: a 30-step conversation's replay shrinks to the expected size, and the bytes are identical
    across two builds.
  - **Status 2026-10-04:** done and measured (master plan P11.11, `app/ai/digest.py`). The first
    defaults (keep 12, block 6) were *worse than doing nothing* at a 90 % cache discount; the sweep
    in `tests/test_ai_replay_digest.py` moved them to keep 8, block 24. Whether the model reasons as
    well from a digest is unmeasured: THE QUEUE H10.
- [x] **1.8 Window by tokens, not by message count.** [Linux] **M** — **NEW** (E16) → P11.12
  - `ai_max_context_messages=40` and `ai_summarise_after_messages=30` count *messages*. One message
    can be 6,000 characters or 20.
  - Do: give the window a token target, estimated from stored lengths or the provider's usage, and
    trigger the summary fold on tokens.
  - Keep the invariant `context.py` documents: a window never starts partway through a tool-call pair.
  - **Status 2026-10-04:** done (master plan P11.12, `app/ai/tokens.py`, `AI_CONTEXT_TOKEN_BUDGET` /
    `AI_SUMMARISE_AFTER_TOKENS`). Estimated from stored lengths; the provider's own usage is still
    what bills.
- [x] **1.9 Measure the state block and cap it.** [Linux] **S** — **NEW** → P11.13, P11.14
  - It is rebuilt every turn (`Kryova-backend/app/ai/state.py`, 629 lines) and sits last on purpose.
  - Add a test that measures its size for a realistic conversation and fails if it grows past a
    measured cap.
  - **Status 2026-10-04:** done (master plan P11.13): 4,663 characters realistic, capped at 6,000;
    four unbounded parts bounded; and the exact operation count that moved the block on every
    CATIA step is banded. The structural fix (the block behind the tool chain) is P11.14, not
    started, behind THE QUEUE H11.
- [ ] **1.10 Re-measure the step budget.** [Seat] **S**
  - `DEFAULT_MAX_STEPS = 60` (`Kryova-backend/app/ai/agent.py:92`). Read the Phase 0 step counts per
    level, and set the default near the 95th percentile of successful turns, plus a margin. A turn
    that needs more is a turn to split (2.4), not a budget to raise.
  - **Status 2026-10-04: BLOCKED on 0.3** (the ladder's step counts). `turn_metrics` now records
    `rounds` and `step_budget` per turn, so the 95th percentile is one query once there are
    successful turns to take it over; nothing is left to build, and a number set without those
    turns would be a guess.
- [x] **1.11 Prompt-cache hit rate as a tracked number.** [Linux] **S**
  - Once 1.1 lands, show the hit rate per turn in the admin console
    (`Kryova-frontend/src/app/dashboard/admin/_components/operations-console.tsx`) and alert when it
    falls, because a drop means something volatile has crept ahead of the state block.
  - **Status 2026-10-04:** done (master plan P11.15, `app/ai/cache_health.py`, `GET /admin/health`
    gaining `ai_cache`, the console's `ai-cache-panel.tsx`). The rate is token-weighted; an alert
    needs a healthy earlier stretch and a recent one that fell under 60 % of it, so a provider that
    reports no cache is "not measured", not an incident. Thresholds are choices to re-read against
    the first week of live numbers (THE QUEUE H1/H7).

### 1C. Spend fewer turns

- [x] **1.12 Batch the agent's tool calls where the provider allows parallel calls.** [Linux] **M** (verify)
  - The loop appears to run tool calls one at a time (`agent.py` around line 855). Read-only calls in
    the same step can run concurrently: measurements, lookups, `search_documentation`.
  - CATIA calls cannot, because the bridge allows one call in flight per device
    (`Kryova-backend/app/catia/connection.py` `_Turnstile`).
  - Test: two read-only calls in one model turn take the time of one.
  - **Status 2026-10-04: verified, and closed without building it** (`docs/MAKING_IT_FASTER.md` §3
    has the numbers). The loop does run a step's calls one at a time. Nine lookups were timed and
    each answers in under 2 ms, so overlapping them saves about a millisecond per extra call
    against a model step of seconds. The four tools that could take real time (`draft_load_case`,
    `assess_fatigue`, `check_part`, `wait_for_simulation`) were **not timed**, and running any of
    them concurrently would be unsafe: a `ToolBox` shares one non-thread-safe `Session`, and
    `Tool.mutating` is the confirmation gate, not a read/write flag (`create_project` and
    `update_project` are ungated writes). The test the item asked for would have asserted a saving
    that was never measured. The lever that does exist is fewer model steps (1.13); whether the
    model batches independent reads when told to is THE QUEUE H12.
- [~] **1.13 Let one tool call carry a whole design plan.** [Linux then Seat] **L** → E15.1, Phase 5.3
  - The design IR (`Kryova-backend/app/design/`) already compiles a spec into a plan. Expose
    "compile and build this spec" as one tool, so a part costs one agent step instead of twenty.
    This is the largest token saving available, because it removes whole turns.
  - **Status 2026-10-04: the Linux half is done; the seat half is not** (master plan P11.16,
    `ToolBox._build_design`). `build_design` compiles the recorded design and runs its plan through
    `_call_catia`, the path every geometry tool takes, so the document binding, the checkpoints and
    the `CatiaOperation` log apply as to a hand build. It stops at the first feature that fails and
    names it (what was built stays), reads the stop button between two features, refuses a part the
    conversation has already started, and is mutating (needs confirmation). Proved on the open
    kernel through `run_agent` with a scripted model: a three-feature plate is two tool steps, and
    the volume is the closed form. **Not proved:** a real CATIA's per-call latency across a long
    build, and whether a model that is shown the tool uses it (THE QUEUE H13).

---

## Phase 2 — Conversation logic: memory, summary, continue

**Goal.** A design session of hundreds of steps across several days keeps its decisions, never
re-does work, and can always be continued with one click.

What exists today, to build on, not to replace:
- **Rolling window:** `Kryova-backend/app/ai/context.py::window`.
- **Running summary:** `context.py::maybe_summarise`, with `SUMMARY_MAX_TOKENS=1_500` and
  `SUMMARY_MESSAGE_CHARS=1_200`, stored in `Conversation.summary` and `summary_through_sequence`
  (`Kryova-backend/app/models/conversation.py`).
- **State block, rebuilt from the database every turn:** `Kryova-backend/app/ai/state.py`.
- **The factual record of what was done:** `Kryova-backend/app/ai/resume.py`, which reads
  `CatiaOperation`.
- **The plan the model declares:** `Kryova-backend/app/ai/taskgraph.py`.
- **A 10-minute resume buffer for a turn in flight:** `Kryova-backend/app/ai/turn_events.py`.

- [x] **2.1 A structured summary, not prose.** [Linux] **M** — **NEW** (E16)
  - Today the fold is an LLM paraphrase. Split it into two parts:
    - **Facts the server already holds**: decisions taken, parameter values, open items, failed
      operations. Built from `resume.py`, `taskgraph.py`, the design record
      (`Kryova-backend/app/core/designs.py`) and requirements. Deterministic, costs no tokens to
      produce, and is byte-stable.
    - **A short model-written note** for intent only, such as "the user prefers aluminium".
  - The current guard `_collapses_history` stays.
  - Test: after a fold, every parameter value set earlier in the conversation is in the summary, and
    the facts part is identical across two folds of the same history.
  - **Status 2026-10-04:** done and tested (master plan P11.17, `app/ai/summary_facts.py`, migration `2a00f5443c5f`). The facts are frozen at the fold, not read live: the summary sits ahead of the cached history, so a live fact would re-bill the prompt on every edit.
- [x] **2.2 "Continue" as a first-class action.** [Linux + frontend] **M** — **NEW** (P5)
  - A turn that stops on `step_budget`, `repeated_calls` or `needs_input` (`agent.py`, with
    `_CLOSING_FALLBACK` and `prompts.AGENT_ENDED_EARLY`) currently needs the user to type something.
  - Do: return a typed `next_action` on the final event. The UI then shows a **Continue** button that
    sends a server-defined continuation with the open task-graph items, rather than free text the
    model might misread.
  - Frontend: `Kryova-frontend/src/hooks/use-agent-chat.ts` and
    `Kryova-frontend/src/components/chat/intervention-prompt.tsx`, which today sends the choice text
    as an ordinary message.
  - Test: a turn ended by `step_budget` offers Continue; pressing it resumes the first open task, and
    the transcript records it as a continuation, not as user prose.
  - **Status 2026-10-04:** done and tested on both sides (master plan P11.18, `app/ai/continuation.py`, `Kryova-frontend/src/components/chat/continue-prompt.tsx`). `needs_input` is deliberately **not** continuable: a typed intervention already is its one-click action, and a bare Continue would answer the question by ignoring it.
- [x] **2.3 Make resuming after a long absence work from facts.** [Linux] **S** → P5.2
  - `Kryova-frontend/src/lib/conversation-resume.ts` shows "welcome back" after 30 minutes or when
    work is unfinished. Drive its content from `resume.py`'s loose ends, which are facts, rather than
    from the summary.
  - **Status 2026-10-04:** done and tested (master plan P11.19): `resume.plan` and `resume.design` on the conversation detail, drawn by `resume-notice.tsx`.
- [x] **2.4 Split a request that is too large into turns on purpose.** [Linux] **M** → E16.2
  - When `plan_work` declares more tasks than one turn's step budget can finish, end the turn at a
    task boundary with a progress report and Continue (2.2), instead of running out mid-task.
  - **Status 2026-10-04:** done and tested (master plan P11.18): the loop ends between tasks with `AGENT_TASK_BOUNDARY` when the next would not fit in the rounds left.
- [x] **2.5 Edit and retry the last message; branch a conversation.** [Linux + frontend] **L** — **NEW** (P5)
  - Retry re-runs the last user message after rolling back to the checkpoint taken before it. A
    branch copies the conversation up to a message, with its own CATIA document copy.
  - Trap: the CATIA document is bound per conversation (CLAUDE.md, *A conversation acts on the
    document it owns*). A branch must save the document as a new file, never share one.
  - **Status 2026-10-04:** done and tested (master plan P11.20), backend and web.
    Decided differently from the text above, on purpose: a branch does **not** copy the CATIA
    document (it says so, and the state block says so until the branch has a document of its
    own — "save as a new file" is a bridge operation nobody has run), and a retry does **not**
    roll the document back to a checkpoint (`catia_restore` needs a seat and an approval token):
    a rewind is refused, with the tools named, whenever a mutating tool ran in the turn, and
    offers a branch from before it. Read-only turns rewind cleanly.
- [x] **2.6 Conversation search, pinning and titles.** [Linux + frontend] **S** — **NEW** (P5)
  - Full-text search over titles and user messages, restricted to the user's own conversations
    (tenant scope; another user's conversation is a 404, never a 403).
  - Title generation exists (`Kryova-backend/app/ai/service.py`, `TITLE_MAX_TOKENS=60`).
  - **Status 2026-10-04:** done and tested (master plan P11.21), backend and web. Search is
    `GET /ai/conversations?q=` over titles and the user's own words only; pinning is
    `PATCH /ai/conversations/{id}` with `pinned`, and does not move `updated_at`.
- [x] **2.7 Long-term project memory.** [Linux] **M** — **NEW** (E16)
  - Facts that should outlive one conversation (the house material, the preferred fastener
    standard, the units of the drawing template) belong to the *project*, as rows the user can see
    and edit. They are injected into the state block.
  - Rule: they are data the user owns, never something the model writes silently. Each one is shown
    and confirmed before it is saved.

---

## Phase 3 — Rate limits and quotas

**Goal.** Limits protect the service and the user's wallet, behave the same with one worker or
twenty, and always tell the user what to do next.

Today:
- Per-minute limits in `Kryova-backend/app/core/config.py`:
  - `chat_requests_per_minute=20`
  - `simulation_requests_per_minute=10`
  - `mcp_requests_per_minute=120`
  - `catia_ops_per_minute=60`, plus 600 per hour enforced in `Kryova-backend/app/catia/dispatch.py`
  - `max_concurrent_simulations_per_user=3`
- Backends: `InMemoryBackend`, which is per process, and `RedisBackend`
  (`Kryova-backend/app/api/rate_limit.py`, `redis_url` in config).
- Keys: `limit_key` uses the signed-in user where there is one.

- [x] **3.1 Use Redis wherever more than one worker runs.** [Linux] **S** → P9
  - With `InMemoryBackend`, each worker enforces its own budget, so the real limit is N times the
    configured one. Make production refuse to boot with `InMemoryBackend` and more than one worker,
    the same way it refuses `MAIL_TRANSPORT=console` (`config.py::_harden_production`).
  - The desktop app runs one process, so it keeps the in-memory backend.
- [x] **3.2 Tell the client its remaining budget.** [Linux + frontend] **S** — **NEW**
  - Send `RateLimit-Limit`, `RateLimit-Remaining` and `RateLimit-Reset` headers on every limited
    route, plus `Retry-After` on a 429.
  - Frontend: show "You can send again in 12 s" instead of a generic error
    (`Kryova-frontend/src/lib/api-client.ts`).
- [x] **3.3 Move every limited route onto the principal key.** [Linux] **S** → P1.6
  - Several routes still use the bare IP key `auth_limiter`, as CLAUDE.md's *Known landmines* item 5
    notes. Users behind one office NAT then share one budget.
- [ ] **3.4 Queue simulations instead of refusing them.** [Linux] **M** — **NEW** (E15)
  - The fourth simulation past `max_concurrent_simulations_per_user=3` is refused today. Queue it
    instead, with a visible position, and start it when a slot frees. It still respects the cost
    estimate (`/billing/estimate`) and the plan's limits.
- [ ] **3.5 Limits per plan, not global constants.** [Linux] **M** → P8
  - Read every limit above from the organisation's billing plan
    (`Kryova-backend/app/api/routes/billing.py`, `PUT /billing/plan`), with config values as the
    defaults.
- [ ] **3.6 Handle provider rate limits within a whole turn.** [Linux] **S**
  - The transport already retries 429 with a capped `Retry-After`
    (`Kryova-backend/app/ai/providers/openai_compatible.py`).
  - Add: when retries run out in the middle of a turn, end it with a typed `provider_busy` stop and a
    Continue button (2.2), not an error.

---

## Phase 4 — A desktop app that installs anywhere

**Goal.** A single signed installer that runs on a clean Windows machine with no repositories, no
Node, no Python and no Postgres preinstalled, updates itself, and is not quarantined by Defender.

**The current blocker** (master plan P9.5, P7.1): `Kryova-frontend/src-tauri/src/lib.rs` starts
`next start` and `uvicorn` from checkout paths compiled in by `Kryova-frontend/scripts/desktop-build.mjs`
(`option_env!`), and `tauri.conf.json` loads `frontendDist: "http://localhost:3000"`. An installer
built anywhere else starts nothing.

- [ ] **4.1 Bundle the frontend.** [Both] **L** → P9.5
  - The app uses server components and the middleware in `Kryova-frontend/src/proxy.ts`, so a static
    export is not a drop-in change.
  - Recommended route: set `output: "standalone"` in `Kryova-frontend/next.config.ts`, and ship the
    standalone server plus a pinned Node runtime as a Tauri **sidecar** (`bundle.externalBin`).
    The webview then loads `http://127.0.0.1:<port>` from the bundled server.
  - Alternative: refactor to a static export plus client-side routing. This is cleaner at runtime
    but a large refactor. Decide once, and record it in the plan.
- [ ] **4.2 Bundle the backend.** [Both] **XL** → P9.5, P7.2
  - Ship an embeddable CPython (python-build-standalone), with every wheel from
    `Kryova-backend/requirements.txt` preinstalled, as a sidecar.
  - The heavy native parts must be checked on a clean VM: OCP/OCCT, gmsh, scipy, openpyxl, ezdxf.
  - **Not** PyInstaller: its import scanning has a long record of missing OCP's and gmsh's native
    plugins. **(verify on a clean VM)**
  - `laya`/`torch` stay out (`Kryova-backend/requirements-laya.txt`, decided 2026-10-04).
- [ ] **4.3 Bundle Postgres.** [Both] **L** — **NEW** (P7)
  - Ship the PostgreSQL binaries, create a cluster in `%LOCALAPPDATA%\Kryova\pgdata` on first run,
    create the `NOBYPASSRLS` application role, and run `alembic upgrade head`. Wire this into
    `Kryova-backend/app/core/local_postgres.py`, which already starts `pg_ctl` from lifespan.
  - Keep the server log outside the data directory (CLAUDE.md, *Database* 4a).
- [ ] **4.4 Fix the setup script's three wrong defaults.** [Linux] **S** — **NEW**, found 2026-10-04
  - `Kryova-frontend/scripts/setup.mjs:79` creates `.venv`, but `Kryova-frontend/src-tauri/src/lib.rs:140`
    looks for `venv`.
  - `setup.mjs:109` writes `DATABASE_URL=sqlite:///./kryova_dev.db`, which the backend refuses at
    startup (`_require_postgres`).
  - The CSP in `Kryova-frontend/src-tauri/tauri.conf.json` still allows `http://localhost:11434`, the
    removed Ollama port, and uses `localhost` where Windows needs `127.0.0.1`. Chromium resolves
    `localhost` to `::1` while uvicorn binds IPv4 (CLAUDE.md, *Driving the GUI* 2a).
  - The same applies to `backend_url()` in `lib.rs:37`. **(verify on the seat)**
  - Test: `Kryova-frontend/src/lib/desktop-bundle.test.ts` reads `tauri.conf.json` and asserts no
    `11434` and no `localhost`.
- [ ] **4.5 Code signing.** [Seat] **M**, needs a certificate purchase → P9.5
  - Sign the MSI and the exe (OV, or EV for immediate SmartScreen trust). This is the real fix for
    Defender's `Wacatac.H!ml` quarantine (CLAUDE.md, *The installed desktop app and Microsoft
    Defender*). A rebuild that happens to scan clean is luck, not a fix.
- [ ] **4.6 Signed auto-update.** [Both] **M** → P7.1
  - Tauri v2 updater, offline signing key kept in a hardware token, beta and stable channels, and
    `latest.json` published by the pipeline. Blocked on 4.1–4.3.
- [ ] **4.7 Native desktop features.** [Both] **M** → P7.3, QUEUE G5
  - The TypeScript side exists but nothing calls it: `Kryova-frontend/src/lib/desktop-powers.ts`
    (deep links, local open filter, notification policy).
  - Add the Tauri plugins to `Kryova-frontend/src-tauri/Cargo.toml`: `dialog`, `fs` (scoped),
    `notification`, `deep-link`, `single-instance`.
  - Widen `Kryova-frontend/src-tauri/capabilities/default.json` only as far as each needs.
  - Register the `kryova://` scheme and file associations for `.CATPart`, `.CATProduct`, `.stp` and
    `.step`. Opening one starts a conversation with it attached.
  - Add a tray icon showing bridge status and running jobs. A second launch focuses the existing
    window (single instance).
- [ ] **4.8 Logs that survive a crash.** [Linux] **S** — **NEW**
  - `lib.rs:115` truncates `backend.log` and `frontend.log` on every launch, so a crash report is
    lost on restart. Rotate them instead (keep 5), and add a "Copy diagnostics" button to the setup
    page (`Kryova-frontend/src/app/setup/page.tsx`).
  - Fix the log path on Linux and macOS, which depends on `LOCALAPPDATA`. **(verify)**
- [ ] **4.9 A release pipeline that installs what it builds.** [CI] **M** → P9.5
  - Build on a Windows runner, sign, install the MSI on a clean VM, launch it, wait for `/health`,
    then uninstall and confirm both uninstall entries are gone. Only then publish.
  - `Kryova-frontend/.github/workflows/desktop.yml` today only type-checks the Rust code.

---

## Phase 5 — Kryova ↔ CATIA, both directions

**Goal.** Kryova and CATIA show the same part at the same moment. Edits in either are seen by the
other. A build is fast because it is batched. Destructive steps are approved by a person in the app.
A CATIA crash costs a restart, never the work.

Today:
- The daemon (`Kryova-backend/scripts/catia_bridge/`) dials out over a WebSocket
  (`Kryova-backend/app/api/routes/catia.py`, `/catia/bridge/ws`), one call in flight per device
  (`Kryova-backend/app/catia/connection.py`).
- `Kryova-backend/app/catia/dispatch.py::call_catia` is the single entry point.
- Every mutating call is preceded by an auto-checkpoint (a COM save plus upload), so an edit costs
  about two round trips (`dispatch.py:125` `_NO_AUTO_CHECKPOINT`, `_auto_checkpoint`).
- The app sees CATIA only as PNG captures (`catia_capture_view`) and a status poll
  (`Kryova-frontend/src/hooks/use-catia-status.ts`).

### 5A. CATIA → Kryova: a live view

- [ ] **5.1 Make the daemon actually send events.** [Seat] **L** — **NEW** (P7.2), found 2026-10-04
  - `Kryova-backend/scripts/catia_bridge/bridge.py:182` defines `emit()`, and **nothing calls it**.
  - The server allows `parameters_changed`, `geometry_changed`, `document_opened`, `document_saved`,
    `checkpoint_created` and `export_completed` (`Kryova-backend/app/catia/events.py`,
    `KNOWN_EVENTS`), and the frontend listens (`Kryova-frontend/src/lib/catia-events.ts`). Only the
    server's own `bridge_connected` and `catia_lost` ever arrive.
  - Do: add a watcher thread in the daemon that compares a cheap fingerprint of the bound document
    every few seconds: feature count, parameter values and the save state. It emits on change.
  - Prefer polling over COM event sinks. Sinks need early binding, and generating type libraries has
    broken the bridge machine-wide twice (CLAUDE.md, *Driving CATIA's interface* 3b/3c).
  - The watcher must never call COM while a tool call is in flight. Share the session's lock.
  - Test: the mock (`scripts/catia_bridge/mock_catia.py`) changes a parameter outside a call, and the
    event reaches the SSE stream.
- [ ] **5.2 Tell the agent about manual edits.** [Linux] **M** — **NEW** (E16)
  - When the fingerprint from 5.1 differs from the one recorded after the last Kryova operation, the
    state block (`Kryova-backend/app/ai/state.py`) says "the document was changed in CATIA since
    step N: these parameters moved, this feature is new".
  - Without this, the agent edits a part it believes it knows.
  - Test: a manual change made between two turns appears in the next turn's state block.
- [ ] **5.3 Show the CATIA part in 3D in Kryova.** [Both] **L** → P6.1/P6.5
  - After each geometry change (5.1), export a tessellation through the bridge (STL or 3DXML, both
    measured working on the seat, CLAUDE.md *Exporting from a seat*). Convert it with the existing
    GLB service (P6.1) and stream it to the WebGL viewer
    (`Kryova-frontend/src/components/webgl-stress-viewer.tsx`). This replaces PNG-only captures.
  - Throttle it: at most one export per N seconds, and never during a batch (5.5).

### 5B. Kryova → CATIA: fast, safe, approved

- [ ] **5.4 One checkpoint per batch, not per edit.** [Both] **M** — **NEW** (E15.1)
  - Today every mutation pays a COM save plus upload first. Inside a declared plan (`taskgraph`) or a
    compiled design batch (5.5), take one checkpoint at the start of the batch and one at the end.
  - Rollback granularity becomes the batch, which is also the unit the user approved.
  - Unify the hard-coded `dispatch._NO_AUTO_CHECKPOINT` with the unused
    `Operation.no_auto_checkpoint` field (`Kryova-backend/app/catia/ops/spec.py`), so there is one
    source of truth.
  - Test: a five-step batch makes exactly two checkpoint calls, and a failure in step three restores
    the start checkpoint.
- [ ] **5.5 A batch frame: many operations in one round trip.** [Both] **L** → E15.1
  - Add an `invoke_batch` message to the protocol
    (`Kryova-backend/docs/CATIA_BRIDGE_PROTOCOL.md`; `scripts/catia_bridge/session.py`). It runs the
    operations in order, stops at the first failure, and returns per-operation results.
  - The design IR's compiled plan (`Kryova-backend/app/design/compile.py`, `execute.py`) is the
    natural producer. Late-bound names (`Created(feature)`) are resolved on the daemon side, from
    the results of earlier operations in the same batch.
  - E15.1 has already measured the CATIA half on the seat. Read its status before designing this.
- [ ] **5.6 Design in OCCT, land in CATIA.** [Both] **M** → Decision 1
  - Make the documented intent the default flow: iterate on the open kernel
    (`GEOMETRY_BACKEND=occt`, fast and headless), then **Send to CATIA** replays the final compiled
    plan as one batch (5.5), with a before and after comparison (mass, bounding box, face count).
  - Face counts agree between the two kernels, but edge counts differ by one per closed cylindrical
    face (CLAUDE.md, kernel item 1), so compare faces, never edges.
- [ ] **5.7 Approvals and restore in the UI.** [Linux + frontend] **M** → P7.2
  - The backend issues approval tokens for destructive tools (`Kryova-backend/app/catia/approval.py`;
    `/catia/approvals`). The frontend never calls `/catia/approvals`,
    `/catia/conversations/{id}/checkpoints`, `/catia/documents` or `/catia/launch`. So
    `catia_restore` cannot be approved by anyone through the app.
  - Do: a checkpoint timeline with restore in the bridge panel
    (`Kryova-frontend/src/components/catia-bridge-panel.tsx`), and an approval dialog for destructive
    tiers.
  - Test: the frontend's api-client test plus a backend route test for the full approval round trip.
- [ ] **5.8 `catia_import` rebinds the conversation's document.** [Linux] **S** — **NEW**
  - The `dispatch.py` comments say this is still open: after an import, the binding still points at
    the old document.
  - Test: through `call_catia`, not through the dispatcher directly (CLAUDE.md, *Testing* 8).

### 5C. Seats, crashes and lifecycle

- [ ] **5.9 Wire seat affinity into dispatch.** [Linux] **M** → E15.4
  - `Kryova-backend/app/catia/affinity.py` (pinned, least-loaded, stranded) is imported only by its
    test. `dispatch._online` takes the first device online.
  - Wire it in, and keep the rule that a document whose seat is offline is `STRANDED`. It is never
    rerouted (CLAUDE.md, *Do not* 14).
- [ ] **5.10 Recover from a CATIA crash.** [Seat] **L** → E15.4
  - Detect `catia_lost` (already published), relaunch CATIA through `local_bridge.py`, reopen the
    bound document from its last checkpoint (`ensure_document` reopens from disk), and tell the user
    exactly which steps after the checkpoint must be replayed. `resume.py` knows which.
  - Never replay automatically past the last successful checkpoint.
- [ ] **5.11 The bridge panel as a first-class status surface.** [frontend] **M** → P7.2
  - Show the seat language, CATIA version (`V5-R33`, read in `scripts/catia_bridge/catia_com.py:320`),
    the bound document, the queue depth, pending approvals, the last event and the checkpoint
    timeline (5.7).
- [ ] **5.12 Section E of THE QUEUE: code that can only be written on the seat.** [Seat] **XL** → E1, E2, E3
  - The CATIA side of sheet metal, the four stop gates, and the conduction check against ccx.
  - Where: `Kryova-backend/docs/WINDOWS_VERIFICATION.md` §E.
  - Rules: read the COM parameter flags before calling an unfamiliar method; never brute-force a
    signature on a live seat (CLAUDE.md, *Driving CATIA's interface* 3a).
- [ ] **5.13 CATIA DMU Kinematics as a second dynamics backend.** [Seat] **L** → E9.5 (BLOCKED,
  needs the Kinematics workbench, THE QUEUE E6)

---

## Phase 6 — Performance and full use of the user's PC

**Goal.** A big run uses the cores and memory the workstation has without overcommitting it, and
the app feels instant everywhere except where physics genuinely takes time.

**Read `Kryova-backend/docs/MAKING_IT_FASTER.md` first.** Three things there are binding:
- **More threads does not help.** gmsh is a global singleton behind a lock, CATIA allows one COM call
  at a time, and BLAS is already threaded.
- **An async rewrite of the routes would make things worse.**
- **§4 forbids three trades of the product for speed.**

The real lever is **processes**, behind the `JobQueue` seam.

- [ ] **6.1 Probe the hardware at startup.** [Linux] **S** — **NEW** (E15)
  - Nothing in `Kryova-backend/app/` reads the core count or RAM today, and `job_workers=2` is fixed
    (`config.py:164`). Read logical and physical cores and total and available RAM at lifespan
    (`Kryova-backend/app/main.py`). Log them, and expose them on `/admin/health` and the setup page.
- [ ] **6.2 Derive the worker and thread counts from the hardware.** [Linux] **M** → MAKING_IT_FASTER §2.3
  - Set `job_workers × BLAS threads ≤ physical cores`. Pin `OMP_NUM_THREADS` per worker; CalculiX
    already takes an explicit thread count (`Kryova-backend/app/solve/calculix/run.py:190`).
  - Leave headroom for CATIA, which shares the workstation.
  - Explicit settings always override the derived ones.
- [ ] **6.3 Admit jobs by memory.** [Linux] **M** — **NEW** (E15)
  - Estimate each job's peak RAM from its degrees of freedom and solver choice (direct is about
    O(n^1.5), CG about O(n); the threshold `_ITERATIVE_THRESHOLD_DOF=100_000` is in
    `Kryova-backend/app/solve/linear_static.py`).
  - Start a job only if it fits in available RAM minus a reserve; otherwise queue it (3.4). This is
    what makes several workers safe: "two direct solves at once is how a box runs out of memory".
  - Derive `max_elements=400_000` from RAM as well.
- [ ] **6.4 A process-pool job queue.** [Linux] **L** → E15, MAKING_IT_FASTER §2.4
  - Add a `ProcessPoolJobQueue` beside `ThreadPoolJobQueue` in `Kryova-backend/app/jobs/queue.py`.
    Each process has its own gmsh singleton, so meshing finally runs in parallel and correctly, each
    job gets a hard memory limit, and the GIL stops mattering.
  - Read that file's docstring first: a Celery queue that silently ran jobs inline was removed from
    there.
  - Background jobs own their own database session (CLAUDE.md, *Non-negotiable rules*).
  - Test: two meshing jobs overlap in wall time, and each produces the mesh a single-process run
    produces.
- [ ] **6.5 Run a convergence study's grids at the same time.** [Linux] **M** — **NEW** (E7)
  - The grid levels in `Kryova-backend/app/verify/convergence.py::run_study` (sizes from
    `Kryova-backend/app/simulation/runner.py:451`) are independent meshes and solves. With 6.3 and
    6.4, run them concurrently when RAM allows: a 3-grid study then takes roughly as long as its
    finest grid instead of the sum.
  - The verdict logic does not change. Only the scheduling does.
- [ ] **6.6 Reuse a factorisation across load cases.** [Linux] **M** → MAKING_IT_FASTER §2.5
  - Several load cases on one mesh share the stiffness matrix. Factorise once (`splu`) and
    back-substitute per case.
  - Test: two load cases give the same answers as two separate solves, to round-off.
- [ ] **6.7 Keep warm processes warm.** [Linux] **S** (verify)
  - Measure the import and initialisation time of OCP, gmsh and the BM25 index on the first request
    after boot. Warm them in lifespan if it is noticeable. Never block `/health` on it.
- [ ] **6.8 Lower `Detail` wherever a full measurement is not needed.** [Linux] **S** → MAKING_IT_FASTER §2.1
- [ ] **6.9 Viewer performance at machine scale.** [frontend] **L** → P6.2
  - Level of detail, GLB streaming (`Kryova-frontend/src/lib/scene-streaming.ts` exists with tests
    and is unused), frustum culling, and progressive loading.
  - Target: an assembly of the size the missions build stays smooth on the integrated GPU of a
    typical workstation. Measure frames per second rather than guessing.
- [ ] **6.10 Tune the local Postgres to the machine.** [Seat] **S** — **NEW** (P7)
  - Size `shared_buffers` and `work_mem` from 6.1's RAM figure when the bundled cluster is created
    (4.3).
- [ ] **6.11 GPU: be honest.** No structural solver here runs on the GPU, and none should be written
  (Decision 2: physics is federated, never hand-written). The GPU's job is the viewer. If a federated
  solver with GPU support is adopted later, it comes through the `Solver` seam with its own
  verification.

---

## Phase 7 — Project management

**Goal.** Projects are the unit an engineer thinks in: a machine, with its conversations, parts,
runs, drawings, requirements and people in one place.

Today:
- **Backend:** CRUD in `Kryova-backend/app/api/routes/projects.py`, organisation projects and members
  in `Kryova-backend/app/api/routes/organisations.py`. `Project` has only `name`, `description`,
  `owner_id` and `organisation_id` (`Kryova-backend/app/models/project.py`).
- **Frontend:** a project is created implicitly as "untitled" on the first chat
  (`Kryova-frontend/src/hooks/use-agent-chat.ts:380`). `api.deleteProject` exists
  (`Kryova-frontend/src/lib/api-client.ts:720`) and nothing calls it. There is no rename UI.

- [ ] **7.1 Rename, archive, delete and duplicate.** [Linux + frontend] **M** — **NEW** (P2)
  - Add `archived_at` (migration). Deletion goes through `MediaService`, so shared blobs survive
    (CLAUDE.md, *Every heavy byte goes through app/media/*).
  - Duplicate copies the specs and geometry references, never the CATIA document by reference.
- [ ] **7.2 Name the project instead of leaving it untitled.** [Linux] **S**
  - Name it from the first conversation's generated title, and let the user rename it on the spot.
- [ ] **7.3 One project page with everything.** [frontend] **L** — **NEW** (P5/P6)
  - Conversations, geometry versions, designs and their revisions, simulations, requirements
    coverage, drawings, the technical file and members.
  - Start from `Kryova-frontend/src/app/dashboard/projects/[projectId]/_components/project-content.tsx`.
- [ ] **7.4 Fix project roles for the agent.** [Linux] **M** — the landmine flagged 2026-09-15
  - `ToolBox._project` asks for the *owner* while the HTTP layer admits any *member*
    (CLAUDE.md, *Known landmines* 7). Decide the rule once:
    - **Recommended:** members may run analyses; only an editor role or above may mutate geometry.
    - Move every tool onto `ToolBox._writable_project` or a read variant.
  - Test: a member and a viewer each try one read and one write through the agent.
- [ ] **7.5 Activity feed.** [Linux + frontend] **M** — **NEW**
  - Read from the append-only audit log (`Kryova-backend/app/core/audit.py`): who ran what, approved
    what, and changed which parameter. Paginated, tenant-scoped.
- [ ] **7.6 Search, tags, starred and recent.** [Linux + frontend] **S** — **NEW**
- [ ] **7.7 Project templates from the mission ladder.** [Linux] **M** — **NEW** (E18)
  - Start a project from a rung in `Kryova-backend/app/design/missions.py` (bracket, gearbox stage,
    conveyor and so on). It comes with its spec, requirements and its `unproven` caveats shown.
- [ ] **7.8 Export and import a whole project.** [Linux] **M** — **NEW** (E17/E19)
  - One archive with the specs, STEP files, results with provenance, drawings, the technical file
    (`Kryova-backend/app/core/technical_file.py`) and a manifest of hashes. Import verifies the
    hashes before it accepts anything.

---

## Phase 8 — Interface

**Goal.** The interface explains what the agent is doing, what it costs and what it has not
verified, and it does so in the engineer's language, light or dark.

- [ ] **8.1 A token and cost meter.** [frontend + Linux] **M** — **NEW** (P8)
  - The backend tracks spend (`Kryova-backend/app/ai/usage.py`, `Conversation.prompt_tokens` and
    `completion_tokens`, `/billing/usage`). The frontend shows none of it.
  - Add: per-turn cost on the final event, a running total per conversation, today's budget left in
    the composer (`Kryova-frontend/src/components/chat/composer.tsx`), and a warning at 80%.
  - Show cost after Phase 1.2 lands, so the number is the true price.
- [ ] **8.2 Wire up the six modules that have tests but no caller.** [frontend] **L** → P6.2/P6.4/P6.6, P7.3/P7.4
  - `Kryova-frontend/src/lib/selection-model.ts`, `viewer-interactions.ts`, `scene-streaming.ts`,
    `scalar-field.ts`, `offline-capability.ts` and `desktop-powers.ts`.
  - Each is tested and imported by nothing, so the work exists but the user never sees it.
  - Priority: selection-model, so tree ↔ 3D ↔ spec selection works (P6.6); then viewer-interactions
    (measure, section, explode).
- [ ] **8.3 Results on the geometry.** [frontend] **M** → P6.5
  - Stress, temperature and fatigue damage drawn on the part in the same viewer as the geometry
    (`Kryova-frontend/src/components/webgl-stress-viewer.tsx`), with the convergence verdict and the
    `governing_basis` (centroid or nodes) shown beside the peak value.
- [ ] **8.4 Explain why a turn stopped, and what to do next.** [frontend] **S**
  - Use the typed stop reason and `next_action` from 2.2. The four endings (`step_budget`,
    `repeated_calls`, `needs_input`, `awaiting_approval`) each get their own sentence and button.
- [ ] **8.5 Dark mode.** [frontend] **M** — **NEW**
  - `Kryova-frontend/src/app/globals.css` is light-only. Move colours to tokens, add a
    `prefers-color-scheme` variant and a manual toggle. The WebGL viewer's background and colour maps
    must follow the theme.
- [ ] **8.6 French first, then other languages.** [frontend] **L** — **NEW**
  - The seat is French V5-R33 and the CATIA KB already carries French names. `lang="en"` is
    hard-coded in `Kryova-frontend/src/app/layout.tsx`.
  - Use a tiny in-house message catalogue. Only three runtime dependencies are allowed by doctrine,
    so a new i18n library is a named decision in the master plan.
  - Server error strings stay English for now, and the UI translates its own copy.
- [ ] **8.7 Keyboard shortcuts.** [frontend] **S** — **NEW**
  - Only ⌘K (`Kryova-frontend/src/app/dashboard/_components/sidebar.tsx:80`) exists today.
  - Add: Esc to stop a turn, ⌘Enter to send, ⌘N for a new conversation, ⌘/ for a shortcut sheet.
- [ ] **8.8 A readable step list.** [frontend] **M**
  - Group the steps of one task (from `taskgraph`), collapse successful reads, highlight refusals with
    the tool's own sentence, and show CATIA captures and 3D (5.3) inline.
  - Where: `Kryova-frontend/src/components/agent-step-list.tsx`.
- [ ] **8.9 Attachments end to end.** [frontend + Linux] **M** → P4.6, P4.7
  - Confirm the composer's attach flow reaches the agent's turn (the P4.7 correction), and show what
    the reader extracted, with its locators, before the agent uses it (Decision 8).
- [ ] **8.10 Onboarding that checks the real machine.** [frontend] **S** → P10
  - `FirstRunChecklist` exists. Feed it from the setup health checks
    (`Kryova-frontend/src/app/setup/page.tsx`): Postgres up, CATIA found, bridge paired, model key
    valid, and a first ladder prompt suggested.
- [ ] **8.11 Accessibility pass.** [frontend] **M** — **NEW**
  - Focus order, labels on icon buttons (the sidebar's hover-only delete has no keyboard path, per
    CLAUDE.md 1a), contrast in both themes, and reduced motion.

---

## Phase 9 — Reliability, security and operations

- [ ] **9.1 Back up and restore the bundled database on the desktop.** [Seat] **M** → P9.6
  - A scheduled `pg_dump --enable-row-security` into `%LOCALAPPDATA%\Kryova\backups`, plus a one-click
    restore. The drill script exists (`Kryova-backend/scripts/restore_drill.py`).
- [ ] **9.2 Crash reporting for the desktop app.** [Both] **M** — **NEW**
  - It is opt-in, with the backend log tail and the version, and it never includes attachment
    contents or model transcripts unless the user ticks a box.
- [ ] **9.3 The 450 MB of Dassault PDFs in git history.** [decision] **S** to decide, **M** to do
  - Listed under CLAUDE.md, *Known landmines* item 1. Rewrite the history before the repository is
    shared, and fetch the manuals at setup instead.
  - This is a legal question for the user, not a session.
- [ ] **9.4 Locks that hold across processes.** [Linux] **L** → E15
  - `Kryova-backend/app/assembly/locking.py` is in-process, as are the rate limits until 3.1. Persist
    the product locks in Postgres (advisory locks or a lease table) before more than one worker runs
    in production.
- [ ] **9.5 The flagged items from the 2026-10-04 audit.** [Linux] **S** each
  - `dynamics.chrono.run` is timed and unbilled; decide a price, then add a `SpanMeter`
    (`Kryova-backend/app/core/metering.py`).
  - `laya_decide._get_agent` swallows `pick_device`'s `ValueError`
    (`Kryova-backend/app/ai/laya_decide.py`). A typo in `AI_INTENT_ROUTER_DEVICE` should fail at
    settings load, not disable the router silently.
  - Rigid-body modes are only caught when the load excites them (CLAUDE.md, *Non-negotiable rules*,
    the residual). Add a load-independent check of the fixtures.
- [ ] **9.6 Observability you can read.** [Linux] **M** → P3
  - The spans exist (`Kryova-backend/app/observe/catalogue.py`). Add a small admin view: p50 and p95
    per span, turn cost, cache hit rate (1.11), queue depth, and bridge latency per operation.
- [ ] **9.7 MCP as a curated channel.** [Linux] **M** — **NEW** (E23.3)
  - Expose about 20–40 well-described tools through `Kryova-backend/app/api/routes/mcp.py` instead
    of the full registry, and add server instructions covering units, mutation consent and "never
    quote an unconverged number".
  - Test it once with a real MCP client before announcing it.

---

## Phase 10 — Proof: the 10/10 gate

Only this phase can award the score, and only with evidence that can be opened.

- [ ] **10.1 The full GUI ladder, Levels 1–6, on the shipped installer.** [Seat] **L**
  - Run from the signed installer (Phase 4), not from a checkout, on the hosted model, with
    screenshots and rungs recorded (`Kryova-backend/docs/GUI_PROMPT_LADDER.md`).
- [ ] **10.2 One complete machine, end to end.** [Seat] **XL** → E18
  - Choose one mission machine (`Kryova-backend/app/design/missions.py`). Take it from conversation
    to geometry in CATIA, then analyses with convergence studies, kinematics, fatigue, drawings with
    GD&T, and the technical file.
  - Record every step's cost and time, and every `unproven` caveat that remains.
- [ ] **10.3 External review.** [people] **XL** → E7, E8, E11, E13, E19
  - A mechanical engineer reviews the analyses and the drawings. Someone outside the repository
    reviews the compliance reading.
  - Until then the product says "not reviewed", in the words `Kryova-backend/app/verify/standards.py`
    already uses.
- [ ] **10.4 Publish the public benchmark score, including if it is bad.** [Linux/Seat] **L** → E23.4

### Scorecard

The targets below are **goals to be confirmed against the Phase 0 baseline**, not measurements.
Where a cell says "baseline", the target is set once 0.2 and 0.3 have produced the number.

| Area | Measure | Today | 10/10 target |
|---|---|---|---|
| Agent works | GUI ladder rung reached, per level, over 10 runs | never measured on the hosted model | Levels 1–4 ≥ 9/10 runs; 5–6 ≥ 7/10 |
| Token cost | median prompt tokens per successful turn | baseline | ≤ 50% of baseline at an equal or better rung |
| Cache | share of prompt tokens served from the cache | not recorded (1.1) | ≥ 70% from step 2 onwards |
| Turn length | steps per successful turn | baseline | ≤ baseline, with no ladder regression |
| Visibility | user can see turn cost and budget left | no | yes, in the composer |
| Memory | parameter values survive a summary fold | prose summary | 100% (2.1 test) |
| Continue | every non-final stop offers a one-click continue | no | yes (2.2) |
| Limits | limits correct with N workers | per-process | Redis or refusal to boot (3.1) |
| Desktop | installs and runs on a clean VM | no (build paths baked in) | yes, signed, self-updating, no Defender flag |
| Cold start | double-click to usable chat | baseline (0.4) | ≤ baseline/2 |
| CATIA → Kryova | manual edit visible in the app | never | within a few seconds (5.1) |
| CATIA latency | round trips per edit | ~2 (checkpoint + call) | one checkpoint per batch (5.4, 5.5) |
| Crash | CATIA crash loses work after the last checkpoint only | partial | yes, with a named replay list (5.10) |
| Hardware | job concurrency derived from cores and RAM | fixed at 2 | derived, memory-admitted (6.1–6.4) |
| Study time | 3-grid convergence study wall time | sum of grids | close to the finest grid (6.5) |
| Projects | rename, archive, delete, one-page overview | create only | complete (Phase 7) |
| Interface | dark mode, French, shortcuts, results on geometry | none | all (Phase 8) |
| Proof | one complete machine, externally reviewed | no | yes (10.2, 10.3) |

---

## What not to do on the way

These look like progress and are not. Each is already measured or decided in the repository.

1. **Do not add threads or rewrite the routes as `async`** to "go faster" (`docs/MAKING_IT_FASTER.md` §3).
2. **Do not coarsen meshes, skip convergence studies, or label sampled results as measured** to save
   time (§4).
3. **Do not add a local LLM** for chat, tests or routing without the user's explicit agreement (the
   user's rule, 2026-10-04). Laya stays opt-in through `requirements-laya.txt`.
4. **Do not show the CATIA part by guessing.** Every number shown about it is measured on one of the
   two kernels, with the provenance attached.
5. **Do not let a summary, a memory or a model paraphrase replace the record.** `CatiaOperation`,
   the design revisions and the audit log are the facts. Everything else is a convenience over them.
6. **Do not ship an installer the pipeline has not installed on a clean machine.**
