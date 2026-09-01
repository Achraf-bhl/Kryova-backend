"""Build the reference index from the command line.

    python -m app.retrieval.build              # build from the configured roots
    python -m app.retrieval.build --check      # report status, build nothing
    python -m app.retrieval.build --query "…"  # search the built index

`--check` is what the setup flow calls. It exits non-zero when the index is
missing or stale, so it composes into a script without anyone parsing its
output, and it prints the reason so a human reading the same output knows what
to do about it.

The build is safe to run against a live server. `corpus.build` writes to a
staging directory and swaps it in, so the running process keeps serving the old
index until the new one is complete, then picks it up on its next load.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.core.config import settings
from app.retrieval.corpus import build as build_corpus
from app.retrieval.extract import available_extractors
from app.retrieval.service import knowledge_service


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )


def _check(service) -> int:  # noqa: ANN001 - internal, one call site
    """Report index status. Non-zero exit means "a build is needed"."""
    stats = service.stats()
    sources = service.sources()

    print(f"Sources scanned:  {', '.join(str(path) for path in service.source_dirs)}")
    print(f"Documents found:  {len(sources)}")
    print(f"Index directory:  {service.index_dir}")
    print(f"Extractors:       {', '.join(available_extractors()) or 'NONE'}")

    if not available_extractors():
        print(
            "\nNo PDF extractor is available. Install one:\n"
            "  pip install pypdf            (pure Python, works anywhere)\n"
            "  apt install poppler-utils    (faster, better on multi-column pages)",
            file=sys.stderr,
        )
        return 2

    if not stats.get("available"):
        if not sources:
            # Nothing to index is not a failure: this is the state a fresh
            # clone is in, and the assistant works without a corpus.
            print("\nNo reference documents yet. Add PDFs and rebuild.")
            return 0
        print(f"\nNo index built yet, but {len(sources)} document(s) are waiting.", file=sys.stderr)
        return 1

    print(
        f"Indexed:          {stats['documents']:,} passages "
        f"from {stats['sources']} document(s), {stats['terms']:,} terms"
    )
    if stats.get("stale"):
        print("\nThe documents on disk have changed since this index was built.", file=sys.stderr)
        return 1
    print("Index is up to date.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.retrieval.build",
        description="Build or inspect the assistant's reference index.",
    )
    parser.add_argument(
        "--source",
        action="append",
        type=Path,
        default=None,
        metavar="DIR",
        help="Directory or file to index. Repeatable. Defaults to the configured roots.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report whether the index exists and is current, then exit. Builds nothing.",
    )
    parser.add_argument(
        "--query",
        metavar="TEXT",
        help="Search the existing index and print the results. Builds nothing.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)
    service = knowledge_service()

    if args.check:
        return _check(service)

    if args.query:
        passages = service.search(args.query, limit=settings.knowledge_max_passages)
        if not passages:
            print("No matching passages.", file=sys.stderr)
            return 1
        for passage in passages:
            snippet = " ".join(passage.text.split())[:280]
            print(f"\n[{passage.score:6.2f}] {passage.citation()}\n  {snippet}…")
        return 0

    roots = args.source or service.source_dirs
    if not available_extractors():
        print(
            "No PDF extractor available. `pip install pypdf` or `apt install poppler-utils`.",
            file=sys.stderr,
        )
        return 2

    print(f"Indexing from {', '.join(str(path) for path in roots)} …")
    report = build_corpus(
        sources=roots,
        destination=service.index_dir,
        exclude=service.exclude,
        on_progress=lambda name, count: print(f"  {name}: {count:,} passages"),
    )
    # The running process (if this is being called in-process) still holds the
    # old arrays; drop them so the next query sees the build that just finished.
    service.reload()

    print("\n" + report.summary())
    if report.passages == 0:
        print(
            "\nNothing was indexed. Check that the source directories contain "
            "PDFs with a text layer -- a scanned document needs OCR first.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
