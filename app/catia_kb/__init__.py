"""A structured knowledge base of CATIA V5, in code rather than in a corpus.

The assistant already has a lexical index over a few hundred megabytes of vendor
manuals (`app.retrieval`). This package is the other half, and the two answer
different questions:

- The corpus knows what the Part Design manual says on page 147.
- This package knows that Edge Fillet lives in Part Design's Dress-Up Features
  toolbar, that the French interface calls it `Congé d'arête` and the German one
  `Kantenverrundung`, that it needs P1, that it fails when the radius exceeds
  the *narrowest adjacent face on the propagated chain* rather than the edge you
  clicked, and that Tritangent Fillet is the alternative when a whole face
  should disappear.

Neither substitutes for the other. The corpus has depth this cannot; this has
coverage, precision and structure the corpus cannot, because a manual page is
prose and this is fields.

**What it is used for**, in the order the value arrives:

1. **Query expansion** (`recognise.expand_query`) -- the highest-leverage use.
   A question about "draft angle" gets `dépouille` added before it reaches the
   BM25 index, so the French manuals become reachable from an English question
   and vice versa. Half this corpus is French; without this, half of it is
   invisible to half the questions.
2. **A lookup tool** (`explain_catia_term`) -- structured facts the model can
   ask for by name, so it states a menu path it read rather than one it recalls.
3. **A per-turn brief** (`brief.brief`) -- a few lines beside the user's message
   naming what their words refer to. This is what makes a small local model
   answer "which workbench" correctly without having to decide to call a tool.

**Language.** CATIA's interface language is a per-installation choice and the
manuals are bilingual, so nothing here assumes English. Localised command names
are indexed as ordinary aliases, which means a question typed in German is
understood without anyone detecting the language first. See `languages` for the
rule about what does *not* localise -- the COM automation API, which is why the
CATIA bridge works on any language install.

**Honesty.** Every field is optional and an empty field means "not recorded",
never "not applicable". A missing German translation is reported as missing. A
product code that is community shorthand rather than a catalogue code says so.
This is a reference an engineer acts on, and a confident wrong menu path costs
more than an admitted gap.
"""

from app.catia_kb.brief import brief, describe
from app.catia_kb.languages import (
    LANGUAGES,
    TRANSLATED,
    localised,
    normalise_language,
    translations,
)
from app.catia_kb.licensing import TRIGRAMS, product
from app.catia_kb.recognise import Match, Recognition, expand_query, recognise
from app.catia_kb.registry import Registry, registry
from app.catia_kb.service import (
    CatiaKnowledge,
    catia_knowledge,
    reset_catia_knowledge,
)
from app.catia_kb.types import Disambiguation, Entry, Kind

__all__ = [
    "LANGUAGES",
    "TRANSLATED",
    "TRIGRAMS",
    "CatiaKnowledge",
    "Disambiguation",
    "Entry",
    "Kind",
    "Match",
    "Recognition",
    "Registry",
    "brief",
    "catia_knowledge",
    "describe",
    "expand_query",
    "localised",
    "normalise_language",
    "product",
    "recognise",
    "registry",
    "reset_catia_knowledge",
    "translations",
]
