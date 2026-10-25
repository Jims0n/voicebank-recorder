"""
Spoken-language detection from a final transcript.

Prompt.language records what the PROMPT was written in. Speakers routinely
answer English prompts in Pidgin (and occasionally the reverse), so the
language of the speech has to be read off what was actually said.

The detector is deliberately a transparent rule list rather than a model, so
every decision can be audited: it returns the markers that fired alongside the
label. Rules, in order:

  1. Any Pidgin marker fires        -> "pidgin". Nigerian Pidgin borrows English
     content words freely, but English does not borrow Pidgin function words, so
     a single marker in otherwise English-looking speech still means Pidgin.
  2. Otherwise any English marker   -> "english". These are function words
     Pidgin replaces ("it" -> "am", "is" -> "na"/"dey", "want" -> "wan",
     "i am" -> "i dey").
  3. Otherwise                      -> "ambiguous". Short commands such as
     "check my balance" or "yes" are lexically identical in both varieties.
  4. Empty transcript               -> None (nothing to judge yet).

Spellings follow the house list in LABELLING.md; extend both together.
"""
from typing import NamedTuple

# Single tokens that only occur in Pidgin (LABELLING.md house spellings plus the
# common discourse particles). "e" is the Pidgin pronoun ("e correct", "e no dey").
PIDGIN_WORDS = frozenset(
    "abeg wetin dey wan wey na don oya omo fit sabi comot una sef dem aza e".split())

# Two-token phrases; the first word alone is ordinary English. "i beg" is how
# "abeg" gets typed when the house spelling is not followed.
PIDGIN_PHRASES = frozenset({("how", "far"), ("no", "be"), ("no", "correct"), ("i", "beg")})

# "make you send am", "make i check", "make we go": subjunctive "make" + pronoun.
MAKE_SUBJECTS = frozenset("you i we e una dem am".split())

# Words after "give"/"go" that keep them English: "give me", "go ahead", "go to".
GIVE_ENGLISH_NEXT = frozenset(
    "me him her them us it you my your his their our the a an this that these "
    "those back out up away some all".split())
GO_ENGLISH_NEXT = frozenset(
    "ahead back on to through home and now away out in up down there here for "
    "with the a an my your by over off".split())

# Words that can open an amount. "give tunde five k" is English (double object);
# "send five k give tunde" is Pidgin, where give means "to".
AMOUNT_START = frozenset(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty "
    "sixty seventy eighty ninety hundred thousand million milli a half".split())
AMOUNT_END = (AMOUNT_START - {"a"}) | {"naira", "k"}

# Only consulted when no Pidgin marker fired. "the", "please" and "with" do occur in
# Pidgin, but in practice alongside a Pidgin marker that has already decided the case.
ENGLISH_WORDS = frozenset(
    "it is are was were did does do want have has not what the please with "
    "dont didnt don't didn't i'm im".split())


class LanguageGuess(NamedTuple):
    language: str | None          # "pidgin" | "english" | "ambiguous" | None
    pidgin_markers: list[str]
    english_markers: list[str]


def _pidgin_markers(w):
    found = []
    for i, tok in enumerate(w):
        prev = w[i - 1] if i else None
        nxt = w[i + 1] if i + 1 < len(w) else None
        after = w[i + 2] if i + 2 < len(w) else None
        if tok in PIDGIN_WORDS:
            found.append(tok)
        if nxt and (tok, nxt) in PIDGIN_PHRASES:
            found.append(f"{tok} {nxt}")
        if tok == "make" and nxt in MAKE_SUBJECTS:
            found.append(f"make {nxt}")
        # Object pronoun: "send am", "cancel am". Not "i am", "am i owing", "eight am".
        if (tok == "am" and prev and prev != "i" and nxt != "i"
                and prev not in AMOUNT_START):
            found.append("am (object)")
        # Serial-verb "give"/"go" meaning "to": "send five k give tunde", "send am go gtb".
        if tok == "give" and nxt and nxt not in GIVE_ENGLISH_NEXT and after not in AMOUNT_START:
            found.append("give <name>")
        if (tok == "go" and prev and nxt and nxt not in GO_ENGLISH_NEXT
                and nxt not in AMOUNT_START):
            found.append("go <name>")
        # "send five k enter femi account": enter meaning "into", straight after the
        # amount. English "enter" is preceded by "please"/"to"/"and", not an amount.
        if tok == "enter" and nxt and (prev in AMOUNT_END or prev == "am"):
            found.append("enter <account>")
    return found


def detect_spoken_language(transcript):
    """Classify a lowercase, unpunctuated transcript (see LABELLING.md)."""
    w = (transcript or "").split()
    if not w:
        return LanguageGuess(None, [], [])
    pidgin = list(dict.fromkeys(_pidgin_markers(w)))
    english = list(dict.fromkeys(t for t in w if t in ENGLISH_WORDS))
    # Copula "i am" (Pidgin says "i dey"); "send am" is handled as a Pidgin object above.
    if any(a == "i" and b == "am" for a, b in zip(w, w[1:])):
        english.append("i am")
    if pidgin:
        return LanguageGuess("pidgin", pidgin, english)
    return LanguageGuess("english" if english else "ambiguous", [], english)
