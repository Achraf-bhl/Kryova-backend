"""A token count estimated from a length -- and labelled as an estimate.

The vendor's tokenizer is the only exact answer, and it is not here: this runs in the
window and fold decisions (`app/ai/context.py`), which fire before any call, and in
`scripts/schema_report.py`, which runs with no network. Both want the same ratio, so it
lives in one place. A figure derived from it is an *estimate* wherever it is printed, and
the provider's own `usage` (`TokenUsage`) is what the ledger bills from.

3.6 characters per token is what JSON-with-prose costs on a typical BPE tokenizer. It is
deliberately a little pessimistic for English prose (~4) and optimistic for dense
numbers and identifiers (~2.5), because the transcript is mostly the former by volume
and the latter by importance; a decision that must not be wrong in one direction
(`ai_context_token_budget` is a ceiling) carries its own margin instead of a tuned ratio.
"""

from __future__ import annotations

import math

CHARS_PER_TOKEN = 3.6

#: What a message costs beyond its text: the role and the framing a provider adds.
MESSAGE_OVERHEAD_TOKENS = 4


def estimate(chars: int) -> int:
    """Tokens for `chars` characters, rounded up so a sum never under-counts."""
    return math.ceil(max(0, chars) / CHARS_PER_TOKEN)
