"""Produce campaign copy in every target locale.

Each locale is authored from the shared :class:`CreativeStrategy`, never
translated from the English output. Three length variants are requested per
headline so the typesetter can *select* a line that fits rather than shrink
type below legibility -- Indic scripts run 15-40% longer than English for the
same meaning, and Tamil is routinely the one that overflows.

Copy generation costs no image quota, which is why adding a language is cheap
and adding an aspect ratio is not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from app.copy.languages import Locale, char_budget, get_locale
from app.copy.strategy import BrandKit, CreativeStrategy

HEADLINE_BUDGET_EN = 34
SUBHEAD_BUDGET_EN = 44
CTA_BUDGET_EN = 18


@dataclass
class LocaleCopy:
    locale: str
    headline: str
    subhead: str = ""
    cta: str = ""
    caption: str = ""
    hashtags: tuple[str, ...] = ()
    alt_text: str = ""
    #: Shorter headline variants, longest first, for the auto-fitter.
    headline_alternates: tuple[str, ...] = ()
    #: Literal English rendering, so a reviewer who cannot read the script can
    #: still check meaning. Not optional -- it is the only practical safety net
    #: for the languages where we have no OCR verification.
    back_translation: str = ""
    needs_review: bool = True


@dataclass
class CopyPack:
    strategy: CreativeStrategy
    by_locale: dict[str, LocaleCopy] = field(default_factory=dict)

    def __getitem__(self, locale_key: str) -> LocaleCopy:
        return self.by_locale[locale_key]


class CopyWriter(Protocol):
    async def write(
        self,
        strategy: CreativeStrategy,
        brand: BrandKit,
        locales: tuple[str, ...],
    ) -> CopyPack: ...


def build_prompt(
    strategy: CreativeStrategy, brand: BrandKit, locales: tuple[str, ...]
) -> str:
    """The transcreation instruction sent to the Foundry chat model."""
    budgets = {
        key: {
            "headline": char_budget(HEADLINE_BUDGET_EN, key),
            "subhead": char_budget(SUBHEAD_BUDGET_EN, key),
            "cta": char_budget(CTA_BUDGET_EN, key),
        }
        for key in locales
    }
    targets = [
        {
            "key": key,
            "language": get_locale(key).language,
            "script": get_locale(key).script,
            "register": get_locale(key).register,
            "label": get_locale(key).label,
            "occasion": strategy.occasion_for(key),
        }
        for key in locales
    ]

    return f"""You are a senior copywriter for the Indian market, writing as a native \
writer in each target language.

You are transcreating, not translating. Keep the intent, the emotional effect \
and the call to action; change the metaphor, the idiom, the sentence structure \
and the rhythm to whatever actually lands in that language. If a wordplay does \
not exist in the target language, invent a different device rather than \
explaining the English one.

STRATEGY
{json.dumps({
    "proposition": strategy.proposition,
    "benefit": strategy.benefit,
    "objective": strategy.objective,
    "register": strategy.register,
    "urgency": strategy.urgency,
    "cta_intent": strategy.cta_intent,
    "facts": list(strategy.facts),
}, ensure_ascii=False, indent=2)}

BRAND VOICE
name: {brand.name}
tone: {", ".join(brand.tone)}
mandatory line: {brand.mandatory_line or "(none)"}

TARGETS
{json.dumps(targets, ensure_ascii=False, indent=2)}

CHARACTER BUDGETS (grapheme clusters, not code points)
{json.dumps(budgets, indent=2)}

HARD RULES
- Respect the character budgets. Count grapheme clusters: a consonant and its \
matra are one unit.
- The occasion named for a locale is the occasion to write to. It differs by \
region on purpose -- write the festival that audience actually celebrates, do \
not carry over the English one.
- Keep numerals, percentages, currency and the words sale/offer/COD/EMI in \
Latin script even inside Indic copy. That is how Indian advertising is \
actually written; Devanagari numerals read as archaic.
- Do not mix scripts within a line except for the above.
- Use the polite register by default (आप / நீங்கள் / আপনি).
- Do not invent any claim, price, date, offer or statistic. Use only STRATEGY.facts.
- For each locale return three headline variants: preferred, shorter, shortest.
- For each locale return a literal English back-translation of the headline so a \
non-speaking reviewer can verify meaning.

Return strict JSON: an object keyed by locale key, each value having \
headline, headline_alternates (2 items), subhead, cta, caption, hashtags, \
alt_text, back_translation."""


class StubCopyWriter:
    """Canned copy, for building and testing the pipeline without a model.

    Mirrors the shape a real writer returns -- including the per-locale
    occasion shift -- so the rest of the pipeline is exercised honestly.
    """

    #: Note the Bengali line: the strategy's occasion is Diwali, but a Bengali
    #: audience's gifting peak is Durga Puja, so the copy names Pujo. That
    #: substitution is the point of transcreation, and a translator would not
    #: make it.
    _SAMPLES: dict[str, dict[str, object]] = {
        "en": {
            "headline": "The festival of purity",
            "alts": ("A purer festival", "Pure this festival"),
            "subhead": "Cold-pressed coconut oil",
            "cta": "Shop now",
            "back": "The festival of purity",
        },
        "hi": {
            "headline": "शुद्धता का त्योहार",
            "alts": ("शुद्धता का पर्व", "शुद्ध त्योहार"),
            "subhead": "कोल्ड-प्रेस्ड नारियल तेल",
            "cta": "अभी खरीदें",
            "back": "The festival of purity",
        },
        "hi-Latn": {
            "headline": "Shuddhata ka tyohaar",
            "alts": ("Shuddh tyohaar", "Shuddhata"),
            "subhead": "Cold-pressed nariyal tel",
            "cta": "Abhi khareedein",
            "back": "The festival of purity",
        },
        "mr": {
            "headline": "शुद्धतेचा सण",
            "alts": ("शुद्ध सण", "शुद्धता"),
            "subhead": "कोल्ड-प्रेस्ड खोबरेल तेल",
            "cta": "आत्ताच खरेदी करा",
            "back": "The festival of purity",
        },
        "bn": {
            "headline": "বিশুদ্ধতার উৎসব",
            "alts": ("বিশুদ্ধ পুজো", "বিশুদ্ধতা"),
            "subhead": "কোল্ড-প্রেসড নারকেল তেল",
            "cta": "এখনই কিনুন",
            "back": "The festival of purity (written to Pujo, not Diwali)",
        },
        "ta": {
            "headline": "தூய்மையின் திருவிழா",
            "alts": ("தூய்மைத் திருவிழா", "தூய்மை"),
            "subhead": "செக்கு தேங்காய் எண்ணெய்",
            "cta": "இப்போதே வாங்குங்கள்",
            "back": "The festival of purity",
        },
        "te": {
            "headline": "స్వచ్ఛత పండుగ",
            "alts": ("స్వచ్ఛ పండుగ", "స్వచ్ఛత"),
            "subhead": "కోల్డ్ ప్రెస్డ్ కొబ్బరి నూనె",
            "cta": "ఇప్పుడే కొనండి",
            "back": "The festival of purity",
        },
    }

    async def write(
        self,
        strategy: CreativeStrategy,
        brand: BrandKit,
        locales: tuple[str, ...],
    ) -> CopyPack:
        pack = CopyPack(strategy=strategy)
        for key in locales:
            sample = self._SAMPLES.get(key)
            if sample is None:
                raise ValueError(f"stub copy writer has no sample for {key!r}")
            locale = get_locale(key)
            pack.by_locale[key] = LocaleCopy(
                locale=key,
                headline=str(sample["headline"]),
                headline_alternates=tuple(sample["alts"]),  # type: ignore[arg-type]
                subhead=str(sample["subhead"]),
                cta=str(sample["cta"]),
                caption=_caption(strategy, brand, locale),
                hashtags=_hashtags(strategy, locale),
                alt_text=f"{strategy.proposition} — {brand.name}",
                back_translation=str(sample["back"]),
                # Every machine-written locale starts unreviewed. For the
                # scripts with no OCR coverage this flag is the only gate
                # between a model and a published brand asset.
                needs_review=True,
            )
        return pack


def _caption(strategy: CreativeStrategy, brand: BrandKit, locale: Locale) -> str:
    occasion = strategy.occasion_for(locale.key)
    lead = f"{strategy.proposition} {('— ' + occasion) if occasion else ''}".strip()
    tail = brand.mandatory_line
    disclosure = _AI_DISCLOSURE.get(locale.key, _AI_DISCLOSURE["en"])
    return "\n\n".join(part for part in (lead, strategy.benefit, tail, disclosure) if part)


#: Platforms strip metadata on upload, so the caption line is the only
#: disclosure channel that reliably survives to the viewer.
_AI_DISCLOSURE = {
    "en": "Created using AI.",
    "hi": "एआई से बनाया गया.",
    "hi-Latn": "AI se banaya gaya.",
    "mr": "एआयने तयार केले.",
    "bn": "এআই দিয়ে তৈরি।",
    "ta": "AI மூலம் உருவாக்கப்பட்டது.",
    "te": "AI ద్వారా రూపొందించబడింది.",
}


def _hashtags(strategy: CreativeStrategy, locale: Locale) -> tuple[str, ...]:
    base = ["#coldpressed", "#coconutoil", "#cleaneating"]
    if strategy.occasion_for(locale.key):
        base.append("#" + strategy.occasion_for(locale.key).lower().replace(" ", ""))
    return tuple(base)
