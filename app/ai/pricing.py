"""What a model call cost, in money -- or that nobody knows.

`app/ai/usage.py` counts tokens, and a token is not a unit anybody pays in. A
hosted model bills fresh input, cached input and output at three different rates
(DeepSeek and Anthropic charge a prompt-cache read at a fraction of fresh input),
so a daily budget that counts every token the same stops a user who has done a
great deal of cheap, well-cached work while letting through one who has done a
little expensive work. This module turns a `TokenUsage` into dollars.

**The price list is configuration, never code** (`settings.ai_prices`). Moving
from DeepSeek to OpenAI changes `.env` and nothing else. And an absent price is
*unknown*, not zero: `cost_micro_usd` returns `None`, the ledger stores NULL, and
a cost budget simply cannot see that call. The alternative -- treating a model
nobody priced as free -- would let the one misconfiguration that matters, a
renamed model, switch every budget off without a sound. `Settings` refuses to
boot production in that state; a development machine gets a log line instead.

Money is integer **micro-dollars** (1e-6 USD). A tokens-times-price product is
exact in them (a price in dollars per million tokens, times a token count, *is*
micro-dollars), so a period total is an exact `SUM` and not a float that drifts
by a cent over a month -- the reason `app/core/metering.py` keeps its quantities
as scaled integers too.
"""

import logging
from decimal import ROUND_HALF_UP, Decimal

from app.ai.provider import TokenUsage
from app.core.config import ModelPrice, settings

logger = logging.getLogger(__name__)

#: Micro-dollars in one dollar.
MICRO = 1_000_000


def price_for(model: str) -> ModelPrice | None:
    """The configured price for `model`, or None when nobody entered one.

    Exact name first, then a case-insensitive match: vendors echo model names in
    their own casing, and a price that silently fails to apply over a capital
    letter is the "budget that never trips" failure in its quietest form.
    """
    prices = settings.ai_prices
    if model in prices:
        return prices[model]
    lowered = model.lower()
    for name, price in prices.items():
        if name.lower() == lowered:
            return price
    return None


def cost_micro_usd(usage: TokenUsage, model: str) -> int | None:
    """What `usage` cost on `model`, in micro-dollars; None when `model` is unpriced.

    Fresh input at the input price, cache reads at the cached price (the input
    price when none was configured), output at the output price. A call that used
    no tokens at all is a priced zero, not an unknown -- the model has a price and
    nothing was spent.
    """
    price = price_for(model)
    if price is None:
        return None
    cached_price = price.cached_input if price.cached_input is not None else price.input
    total = (
        price.input * usage.fresh_prompt_tokens
        + cached_price * usage.cached_prompt_tokens
        + price.output * usage.completion_tokens
    )
    return int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def usd(micro: int | None) -> Decimal | None:
    """Micro-dollars as a `Decimal` dollar amount, None staying None."""
    return None if micro is None else Decimal(micro) / MICRO


def budget_micro(dollars: Decimal) -> int:
    """A configured dollar budget as micro-dollars. Zero or negative means unlimited (0)."""
    return max(0, int((dollars * MICRO).quantize(Decimal(1), rounding=ROUND_HALF_UP)))
