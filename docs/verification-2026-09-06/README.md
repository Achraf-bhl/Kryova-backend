# Verification session — 2026-09-06

Driving the real Kryova GUI with the ladder in
[../GUI_PROMPT_LADDER.md](../GUI_PROMPT_LADDER.md).

## Setup as measured at the start of this session

| Thing | State |
|---|---|
| Backend | uvicorn on 127.0.0.1:8000, `/health` ok (database ok, media_store ok), git sha `5dc11e9` |
| Frontend | Next.js on localhost:3000 |
| Database | Neon, migrations at head, `alembic check` reports no drift |
| Geometry backend | `GEOMETRY_BACKEND=catia` |
| CATIA | V5-R33, running, French seat |
| Model | `qwen3.5:9b` via Ollama 0.33.3 |
| GPU | RTX 5070 Laptop, 8151 MiB. Model 19% CPU / 81% GPU at `num_ctx=32768`, 7.8 GB resident |
| Documentation index | 4,920 passages from 25 documents, up to date |
| Browser | Edge 152 attached over CDP:9222, signed in as `claude.admin@kryova.dev` |

## Why `qwen3.5:9b`

Probed against Ollama's `/api/chat` before being made the main model, because CLAUDE.md's
technology-register rule applies to models too — advertising `tools` is not evidence of emitting
them, and that exact failure is what took `gpt-oss:20b` out at 108 tools.

| Model | Tools in payload | Result | Time |
|---|---|---|---|
| `qwen3.5:9b` | 2 | structured `tool_call` to `catia_new_part`, empty content | 14.9 s |
| `qwen3.5:9b` | 108 | structured `tool_call` to `catia_new_part`, empty content | 8.4 s |
| `qwen3-coder:30b` | — | works, but 22 GB on an 8 GB card: 72% CPU, ~150 s for a one-word reply | — |

GPU offload against context window, measured on this machine with the desktop and browser also
holding VRAM:

| `num_ctx` | Split | Resident |
|---|---|---|
| 32768 | 19% CPU / 81% GPU | 6.8 GB |
| 24576 | 16% CPU / 84% GPU | 6.6 GB |
| 16384 | 14% CPU / 86% GPU | 6.4 GB |

Halving the window buys five percentage points, because the 6.6 GB of weights are the bulk and
not the KV cache. So the full 32k window is kept: a truncated prompt is refused loudly by
`app/ai/providers/ollama.py`, and losing a long CATIA run to a short window costs far more than
the 5%. `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0` are set for the user.

## Results

Ladder runs are logged in [../GUI_PROMPT_LADDER.md](../GUI_PROMPT_LADDER.md) itself, which is
the run log as well as the script. Screenshots for the early prompts are in [shots/](shots/);
the later ones are named for their prompt in this directory.

This session ran past midnight and into 2026-09-07. Two things run after midnight are recorded
here rather than in the ladder, because neither is a ladder prompt.

### GEAR — geared motor shaft, off-ladder (2026-09-07, ~00:45)

A rung 2–3 prompt written to need several features that must agree: a Ø30 × 260 steel shaft, a
24-tooth module-4 spur gear on the same axis 60 mm from one end, the blank Ø104 × 25 wide, one
tooth gap cut and patterned 24 times at 15°, a 8 × 4 mm keyway under the gear, then *measure it
and report the mass* and show an isometric view. Kept because it is the cheapest prompt found so
far that reaches the pattern path and the measure-and-report loop in one turn.

**Two attempts, both failed, each naming a different defect.**

1. The fuller attempt built the shaft and the gear blank, and then every further
   `catia_sketch_create` was refused — "this part already holds 2 sketches with nothing drawn in
   them" — so the teeth were never cut. Reading the parts back on the seat showed the guard was
   right about the count and wrong about the cause: the two *named* sketches held one element
   each (the absolute axis, i.e. nothing) while `Esquisse.2` and `Esquisse.4` held fourteen
   apiece. `sketch_revolve_profile` and friends build their own sketch, so the bridge was making
   the debris it then blamed the agent for. Fixed in `8fc2bd8` — an empty sketch on the support
   that was asked for is renamed and handed over.
2. The attempt still in the database (`1caae5e9`, 7 messages) died earlier and for an unrelated
   reason: `catia_new_part` and `catia_sketch_create` both succeeded, and then the model returned
   an **empty turn** — no text, no tool call. `AGENT_EMPTY_TURN_AFTER_WORK` did its job and said
   so; the model went silent a second time and the turn ended. That is the local model being the
   limit, which CLAUDE.md says to expect and to report as such rather than as a product defect.

**Neither has been re-run since the E16 fixes landed.** GEAR and PRO4 are the two to re-run
first at the next gate.

### PRESS / PRO4 screenshots

`PRESS-catia.png` and `PRESS-screen.png` are the punch-press run written up under **PRO4** in the
ladder. They are named for the machine rather than the prompt because they were taken while
chasing the defect, before it was clear which ladder entry they belonged to.

`GEAR-screen.png` is **not** a run screenshot — it is the editor, capturing the token-budget
measurement that became `tests/test_naming_rule.py`. Kept because that measurement is the reason
the tool-schema budget has a ceiling at all, and deleted evidence is worse than mislabelled
evidence; renaming it now would break the reference from the commit that used it.
