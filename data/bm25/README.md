# Reference manuals

This is where the CATIA and FEA documentation the assistant consults lives.

```
data/bm25/
  sources/   <- put PDFs here
  index/     <- built artifacts, rebuildable, never edited by hand
  README.md  <- this file
```

Nothing in here is committed except this README. The manuals are vendor
copyrighted material and hundreds of megabytes; the index is derived from them
and can always be rebuilt.

## Adding documents

Drop them in `sources/` and rebuild:

```bash
python -m app.retrieval.build
```

`data/` itself is also scanned, so manuals that were already sitting there
before this directory existed are picked up without moving anything.

Accepted: `.pdf`, `.txt`, `.md`, `.markdown`, `.rst`. Sub-directories are
walked, so organising `sources/` by workbench or language is fine.

## Checking it

```bash
python -m app.retrieval.build --check            # is there an index, is it current
python -m app.retrieval.build --query "fillet"   # what would the assistant find
```

`--check` exits non-zero when a rebuild is needed, so it composes into a script.
It is what `scripts/setup.sh` calls.

## Rebuilding is safe while the server is running

The build writes to a staging directory and swaps it in only when complete, so
a running server keeps serving the previous index until the new one is ready.
There is no moment where a half-written index is live.

## If a document does not appear

Run `--check` first; it names every extractor available on the machine.

- **`scanned, no text layer`** — the PDF is a photograph of paper. No extractor
  can read it and no package will fix it; it needs OCR before it can be
  indexed. Several of the large French training manuals are in this state.
- **`no extractor could read it`** — nothing on this machine can parse PDFs, or
  the file is corrupt. Install one:
  ```bash
  pip install pypdf              # pure Python, works anywhere (in requirements.txt)
  apt install poppler-utils      # faster and better on multi-column pages
  ```
  Both are used when present: poppler first for speed and layout, `pypdf` as the
  portable fallback.
- **Nothing at all is found** — check that `sources/` actually contains files
  with one of the accepted extensions.

A document that cannot be read is skipped and named in the build report. It
never fails the build; the other manuals still index.

## Language

English and French manuals share one index and either language searches both.
Accents are folded, so `epaisseur` typed without accents finds `épaisseur`.
