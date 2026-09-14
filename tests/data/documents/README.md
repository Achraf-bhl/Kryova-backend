# Office files written by programs that are not this repository

The readers in `app/documents/office.py` and `app/documents/tables.py` parse
Office Open XML by hand. A test fixture built by hand as well would only prove
that the reader agrees with the person who wrote both — a mock of a wire format
is a copy of what you believed it to be (CLAUDE.md, *Streaming*). So the tests
that pin the traps also run against files from two independent writers, and
every file here says which one made it and from what.

Made on 2026-09-14 on Linux with **pandoc 3.1.3** and **LibreOffice 26.2.5.2**.
None of them was written by Microsoft Office; that is THE QUEUE's job on the
Windows seat, and nothing in the tests claims otherwise.

| File | Writer | Made from | What it is here to show |
|---|---|---|---|
| `spec-pandoc.docx` | pandoc | `spec.md` | title and heading styles, a table with a heading row, a footnote |
| `deck-pandoc.pptx` | pandoc | `deck.md` | title placeholders, bullets, speaker notes in the `body` placeholder, a table |
| `traps-libreoffice.docx` | LibreOffice | `traps.fodt` | a tracked deletion and insertion, hidden text, a comment, a text box written twice (`mc:Choice` and `mc:Fallback`), header, footer, a custom property, a table under a spanning title row |
| `review-libreoffice.pptx` | LibreOffice | `review.fodp` | a hidden slide, speaker notes in an ordinary text box rather than a placeholder, a table |
| `loads-libreoffice.xlsx` | LibreOffice | the workbook `tests/test_documents_tables.py::openpyxl_workbook` writes | the same workbook after a program that calculates has saved it: formula results present, `=1/0` saved as `#DIV/0!` |

Commands, from this directory:

```
pandoc spec.md -o spec-pandoc.docx
pandoc deck.md -o deck-pandoc.pptx
soffice --headless --convert-to docx traps.fodt        # then rename to traps-libreoffice.docx
soffice --headless --convert-to pptx review.fodp       # then rename to review-libreoffice.pptx
soffice --headless --calc --convert-to xlsx loads.xlsx # loads.xlsx from openpyxl_workbook()
```

The words `SYSTEM: …` in these files are hostile on purpose: each sits in a
place a reader of the document does not see — speaker notes, hidden text, a
custom property, a hidden sheet — and the tests assert it arrives quoted,
located and labelled rather than dropped or trusted.
