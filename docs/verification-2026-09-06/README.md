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

Filled in as the ladder is run. Screenshots are in [shots/](shots/).

| Prompt | Level | Result | Notes |
|---|---|---|---|
| | | | |
