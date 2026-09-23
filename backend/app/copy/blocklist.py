"""Terms the image provider refuses outright, and what to say instead.

``men's`` is on Azure's BingBlockList. Not a phrase near it, not a
combination with something else -- the bare word. Probed against the live
filter, same sentence otherwise:

    "casual shoes on a wooden table"   -> accepted
    "mens shoes on a wooden table"     -> refused, BingBlockList_Prompt

Presumably the list targets adult-search phrasing and a retail possessive
collides with it.

It is worth naming how bad that failure was. Every brief for menswear --
shoes, shirts, watches, a large part of Indian D2C -- was refused before a
pixel was drawn. The refusal arrived as an unexplained HTTP 400, and nothing
in the product told anyone which of two hundred words was at fault.

Dropping the possessive costs almost nothing. The rest of a compiled brief
("full-grain tan leather, stitched welt, rounded toe, low heel") already
describes the shoe precisely, and keeping the campaign is worth more than
keeping a word the service will not accept.

This lives in its own module because the patterns are fiddly and the cost of
getting one subtly wrong is silent: a pattern that matches nothing restores
the original bug, and one that over-matches quietly rewrites the brief.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: Typewriter and typographic apostrophes both appear: a user types the
#: first, a language model writes the second.
_APOSTROPHE = r"['’]?"

#: Word boundaries matter in both directions. Without the leading one,
#: "women's" matches the men's pattern and becomes "wo"; without the
#: trailing one, "menswear" is mangled into "wear".
_TERMS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(rf"\b(?:wo)?(?:men|man){_APOSTROPHE}s\b", re.IGNORECASE),
        "",
    ),
)


def sanitise(prompt: str) -> tuple[str, list[str]]:
    """Strip refused terms from a prompt.

    Returns the cleaned prompt and a list of what was removed. The caller is
    expected to surface that list: a prompt quietly rewritten on the way out
    is one nobody can debug, and the removal does change the picture.
    """
    removed: list[str] = []
    for pattern, replacement in _TERMS:
        found = pattern.findall(prompt)
        if found:
            removed.extend(found)
            prompt = pattern.sub(replacement, prompt)

    if removed:
        # Removing a word leaves a doubled space and sometimes an orphaned
        # comma, both of which read as a typo to anyone looking at the prompt
        # in the failure panel.
        prompt = re.sub(r"\s{2,}", " ", prompt)
        prompt = re.sub(r"\s+([,.;])", r"\1", prompt).strip()
        logger.info("removed blocked term(s) from prompt: %s", removed)

    return prompt, removed
