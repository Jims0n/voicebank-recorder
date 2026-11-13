"""
Bring free-form text (raw ASR output, typed input) into the labelling convention
of LABELLING.md, so it can be compared word for word with a reference transcript:

    "Abeg, send ₦5,000 to Tunde's GTB."  ->  "abeg send five thousand naira to tunde's gtb"

- lowercase, punctuation removed (apostrophes inside words kept: "tunde's")
- numbers as words: 5000 -> "five thousand", 150k -> "one hundred and fifty k",
  1.5k -> "one point five k", ₦500 -> "five hundred naira"
- account/phone-like digit strings (8+ digits, or zero-led, or three or more
  chunks like "76 24 72 49 64") read digit by digit
- "9mobile" -> "nine mobile"

Idempotent: text already in the convention comes back unchanged.

This cannot recover how a number was said. Whisper writing "150k" for spoken
"one fifty k" still costs a word error, and that is a real formatting mismatch
the fine-tuned model is expected to learn away.
"""
import re

from nlu.amounts import is_digit_string, standardise_digits

_ONES = "zero one two three four five six seven eight nine".split()
_TEENS = ("ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
          "eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_SCALES = [(1_000_000_000, "billion"), (1_000_000, "million"), (1_000, "thousand")]


def _below_hundred(n):
    if n < 10:
        return [_ONES[n]]
    if n < 20:
        return [_TEENS[n - 10]]
    tens, unit = divmod(n, 10)
    return [_TENS[tens]] + ([_ONES[unit]] if unit else [])


def _below_thousand(n):
    hundreds, rest = divmod(n, 100)
    words = [_ONES[hundreds], "hundred"] if hundreds else []
    if hundreds and rest:
        words.append("and")          # "one hundred and fifty", as the read prompts say it
    return words + (_below_hundred(rest) if rest else [])


def int_to_words(n):
    """1500 -> "one thousand five hundred"; 150000 -> "one hundred and fifty thousand"."""
    if n == 0:
        return "zero"
    words = []
    for value, name in _SCALES:
        count, n = divmod(n, value)
        if count:
            words += _below_thousand(count) + [name]
    if n:
        words += _below_thousand(n)
    return " ".join(words)


def number_to_words(raw):
    """A digit token as it would be spoken under the labelling convention."""
    if is_digit_string(raw):
        return " ".join(_ONES[int(d)] for d in raw)
    whole, _, frac = raw.partition(".")
    words = int_to_words(int(whole))
    if frac:
        words += " point " + " ".join(_ONES[int(d)] for d in frac)
    return words


def normalise_transcript(text):
    t = standardise_digits(text)
    t = re.sub(r"\b9\s*mobile\b", "nine mobile", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)           # keep 1.5, drop sentence dots
    t = re.sub(r"[^\w\s.']|_", " ", t)
    t = re.sub(r"(?<!\w)'|'(?!\w)", " ", t)              # quotes, not apostrophes
    t = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", t)
    # ASR splits account numbers into chunks ("76 24 72 49 64"); read them as digits.
    t = re.sub(r"\b\d{1,4}(?: \d{1,4}){2,}\b",
               lambda m: " ".join(_ONES[int(d)] for d in m.group() if d.isdigit()), t)
    t = re.sub(r"\d+(?:\.\d+)?", lambda m: f" {number_to_words(m.group())} ", t)
    return re.sub(r"\s+", " ", t).strip()
