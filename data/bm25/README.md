# Reference manuals

This is where the CATIA and FEA documentation the assistant consults lives.

```
data/bm25/
  *.pdf      <- the manuals themselves
  sources/   <- or here; both are scanned
  index/     <- built artifacts, rebuildable, never edited by hand
  README.md  <- this file
```

**The manuals are committed; the index is not.** They are tracked on purpose, so
that a `git pull` on the Windows test workstation brings the corpus with it
rather than requiring several hundred megabytes to be copied by hand. They are
third-party Dassault Systèmes material in a repository carrying its own
licence, which is a thing to settle before this repository is published or
cloned widely -- and a later `.gitignore` cannot undo it, only a history
rewrite can. `index/` is ignored: it is derived, it is rewritten whole on every
build, and it would conflict between machines.

## Adding documents

Drop them in this directory (or in `sources/`, if you would rather keep the
top level tidy) and rebuild:

```bash
python -m app.retrieval.build
```

Accepted: `.pdf`, `.txt`, `.md`, `.markdown`, `.rst`. Sub-directories are
walked, so organising by workbench or language is fine. `data/` above this
directory is scanned too, so a manual that predates this layout is picked up
without moving anything.

## Measuring it

`tests/test_retrieval_corpus.py` runs the retriever against whatever is
actually indexed here -- a set of real engineering questions and the manual
that ought to answer each one -- and reports precision@1, precision@3 and MRR.
It skips entirely when no index has been built, so it costs a fresh clone
nothing, and it skips any individual question whose subject matter is not in
the corpus, so curating these files does not turn the suite red.

```bash
pytest tests/test_retrieval_corpus.py -q
```

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
