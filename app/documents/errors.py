"""What can go wrong reading a file a customer handed us, said out loud.

Every error here follows the house rule for messages: it names the file, says
what was tried, and says what the user should do next. "Invalid input" is not an
option -- the person reading it is an engineer who wants their datasheet read,
and the only useful reply is either the content or the sentence that gets them
to the content.

There is a second rule specific to this package, and it is the more important
one: **a refusal is a good outcome and a silent misread is not.** A parser that
guesses at a dimension it could not resolve produces a number nobody can trace
and everybody believes. So the extractors here raise, or record the line they
declined, far more readily than they infer.
"""

from __future__ import annotations


class DocumentError(RuntimeError):
    """Base for every failure in this package."""


class UnsupportedDocument(DocumentError):
    """Nothing in this deployment can read that kind of file.

    Distinct from `ExtractionFailed`: this is decided before anything is read,
    from the file's own bytes, so the message can name the format and say which
    optional package would handle it.
    """


class ExtractionFailed(DocumentError):
    """A reader that should have handled this file could not.

    Carries the same two-message arrangement `app.retrieval.extract.ExtractionError`
    uses, and for the same reason: `str(exc)` is the full diagnostic naming every
    reader tried, `short` is the one clause an attachment list can show beside a
    filename without repeating three paragraphs per row.
    """

    def __init__(self, message: str, *, short: str) -> None:
        super().__init__(message)
        self.short = short


class BoundaryViolation(DocumentError):
    """Someone tried to get extracted text out of the package unquoted.

    This is not a runtime path any correct caller reaches. It exists so that the
    one thing Decision 8 forbids fails loudly at the moment it is attempted,
    rather than producing a string that looks fine and is a prompt injection.
    """
