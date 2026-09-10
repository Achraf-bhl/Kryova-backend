"""The docs site's content, as data (P10.2).

**Named `handbook`, not `docs`, because `app/documents/` already exists** and
means something else entirely — parsing a customer's PDF drawing. Two packages
one letter apart, doing unrelated things, is a mis-import waiting to happen in
every future session, and the cost of avoiding it is one word.

Three things live here, and they share one rule: **nothing is written twice.**

* `gallery` derives the mission gallery from `app.design.missions.LADDER`, the
  suite that actually decides whether this product works.
* `guides` is the task-oriented documentation, and every step it lists that
  names a route is checked against the running application's own router — a
  guide telling somebody to POST to an endpoint that no longer exists is worse
  than no guide.
* `reference` builds the API reference from the OpenAPI document FastAPI
  already serves, rather than from a hand-kept list.

The plan's phrasing for this task is "API reference **from the OpenAPI schema
that already exists**", and the emphasis is the whole design: a docs site is the
part of a product most likely to quietly stop being true, and the only defence
is deriving it from something that cannot drift because it *is* the thing.

## A trap this package walked into once

`gallery.py` defines a function called `gallery()`, and re-exporting it here as
`from app.handbook.gallery import gallery` **rebinds the package attribute**:
after that line, `app.handbook.gallery` is the function, not the module, so
`import app.handbook.gallery as m; m.gallery()` raises `AttributeError` at
request time and nowhere else. The import succeeds, the module loads, the route
500s.

So this file re-exports **types only** and never a callable whose name matches a
submodule. Reach the functions through their modules — `from
app.handbook.gallery import gallery` at the call site is explicit and cannot
shadow anything.
"""

from app.handbook.gallery import GalleryEntry
from app.handbook.guides import GUIDES, Guide

__all__ = ["GUIDES", "GalleryEntry", "Guide"]
