"""
Parse naira amounts out of a transcript, as spoken in Nigerian banking commands.

    parse_amount("abeg send two fifty k give tunde")                      -> 250000
    parse_amount("send one five to palmpay account three seven seven ...") -> 1500

Pure Python, no Django: shared by the ASR evaluation script and the NLU.

Input is a lowercase transcript (LABELLING.md), but digits are accepted too, so
raw ASR output ("₦5,000", "150k", "25 000") parses without a separate pass.

Forms handled
  standard      five thousand naira · one hundred and fifty thousand · a million naira
  k / milli     five k · twenty five k · hundred k · one milli
  decimals      one point five k · two point five million naira
  half          half a million naira
  "X fifty"     one fifty k (150,000) · two fifty k (250,000) · one fifty thousand.
                A lone digit directly before a tens/teens word means hundreds, as
                in English "two fifty" for 250.
  "one five"    1,500. Two bare non-zero digits on their own mean X thousand Y hundred.
  digits        5000 · 5,000 · 25 000 · 1.5k · 150k · 2.5m · ₦5,000

Not amounts
  - Runs of three or more bare digits, or bare runs containing zero/"oh":
    account and phone numbers are read digit by digit. Likewise three or more
    numeric chunks in a row ("76 24 72 49 64"), which is how ASR splits them.
  - A single number word with no scale, "naira" or digits ("that one",
    "send five", "last five transactions"): too ambiguous to call an amount.
  - "nine mobile" / "9mobile" (a network), "millionaire".

When a transcript holds several amounts the strongest wins: one with a scale
word or "naira" (2), then digits or a Nigerian shorthand (1), then bare words like
"twenty five" (0). Among equals the last wins, since speakers self-correct
forwards ("five k... no, ten k").
"""
import re
from typing import NamedTuple

UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine".split())}
TEENS = {w: i + 10 for i, w in enumerate(
    "ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: (i + 2) * 10 for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split())}
SCALES = {"thousand": 1_000, "k": 1_000, "million": 1_000_000, "milli": 1_000_000,
          "billion": 1_000_000_000}
# Colloquial suffixes always end the amount: "one k one k" is two amounts, not 1,001,000.
TERMINAL_SCALES = {"k", "milli"}
DIGIT_WORDS = set(UNITS) | {"oh"}
NUMBER_WORDS = set(UNITS) | set(TEENS) | set(TENS) | {"hundred"}


class Amount(NamedTuple):
    value: int
    start: int        # token offsets into tokenize(text)
    end: int
    strength: int     # 2 scale word or "naira", 1 digits/shorthand, 0 bare words


class _Group(NamedTuple):
    value: float
    end: int
    shorthand: bool   # digits, decimal, "half a", "two fifty", "one five"
    terminal: bool    # cannot take a following scale word


def _is_num(tok):
    return isinstance(tok, float)


def standardise_digits(text):
    """Lowercase, and put digit forms in one shape: "₦5,000" -> "5000 naira",
    "25 000" -> "25000", "150k" -> "150 k", "2.5m" -> "2.5 million"."""
    t = (text or "").lower().replace("’", "'")
    t = re.sub(r"₦\s*([\d.,]+)", r" \1 naira ", t)
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)
    t = re.sub(r"\b(\d{1,3})((?: \d{3})+)\b",
               lambda m: m.group(1) + m.group(2).replace(" ", ""), t)
    t = re.sub(r"(\d)\s*k\b", r"\1 k", t)
    return re.sub(r"(\d)\s*m\b", r"\1 million", t)


def is_digit_string(raw):
    """Account/phone-like: 8+ digits, or zero-led. Read digit by digit, never an amount."""
    return raw.isdigit() and (len(raw) >= 8 or (len(raw) > 1 and raw[0] == "0"))


def tokenize(text):
    """Lowercase word tokens. Numeric literals become floats; digit strings that look
    like account or phone numbers stay strings so they never parse as amounts."""
    t = re.sub(r"\b(?:nine|9)\s*mobile\b", " ninemobile ", standardise_digits(text))
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)
    tokens = []
    for raw in re.sub(r"[^\w\s.']", " ", t).split():
        raw = raw.strip("'")
        if re.fullmatch(r"\d+(?:\.\d+)?", raw):
            tokens.append(raw if is_digit_string(raw) else float(raw))
        elif raw:
            tokens.append(raw)
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.t = tokens

    def at(self, i):
        return self.t[i] if 0 <= i < len(self.t) else None

    def digit_run(self, i):
        j = i
        while self.at(j) in DIGIT_WORDS:
            j += 1
        return j - i

    def chunk_run(self, i):
        j = i
        while _is_num(self.at(j)):
            j += 1
        return j - i

    def tens_tail(self, i):
        """[teen | tens [unit] | unit] -> (value, next_i), or (0, i) if absent."""
        tok = self.at(i)
        if tok in TEENS:
            return TEENS[tok], i + 1
        if tok in TENS:
            nxt = self.at(i + 1)
            if nxt in UNITS and UNITS[nxt] and self.at(i + 2) not in DIGIT_WORDS:
                return TENS[tok] + UNITS[nxt], i + 2
            return TENS[tok], i + 1
        if tok in UNITS and UNITS[tok] and self.at(i + 1) not in DIGIT_WORDS:
            return UNITS[tok], i + 1
        return 0, i

    def decimal(self, value, i):
        """Optional "point five" after an integer part."""
        if self.at(i) == "point" and self.at(i + 1) in UNITS:
            j, frac = i + 1, ""
            while self.at(j) in UNITS:
                frac += str(UNITS[self.at(j)])
                j += 1
            return value + float(f"0.{frac}"), j, True
        return value, i, False

    def group(self, i):
        """One number below a thousand (or a literal) starting at i, or None."""
        tok, nxt = self.at(i), self.at(i + 1)
        if _is_num(tok):
            return _Group(tok, i + 1, True, False)
        if tok == "half" and nxt == "a" and self.at(i + 2) in SCALES:
            return _Group(0.5, i + 2, True, False)
        if tok == "a" and nxt in SCALES:
            return _Group(1, i + 1, False, False)

        value, j = 0, i
        if tok == "a" and nxt == "hundred":
            value, j = 1, i + 1
        elif tok in UNITS:
            run = self.digit_run(i)
            following = self.at(i + run)
            if run >= 3 or "oh" in self.t[i:i + run] or (run == 2 and "zero" in self.t[i:i + 2]):
                return None
            if run == 2:
                if following in ("hundred", "point") or following in TENS or following in TEENS:
                    return None
                return _Group(UNITS[tok] * 1000 + UNITS[nxt] * 100, i + 2, True, True)
            if UNITS[tok] and (following in TENS or following in TEENS):
                tail, k = self.tens_tail(i + 1)
                return _Group(UNITS[tok] * 100 + tail, k, True, False)
            value, j = UNITS[tok], i + 1
        elif tok in TEENS or tok in TENS:
            value, j = self.tens_tail(i)
        if j > i:
            value, j, dec = self.decimal(value, j)
            if dec:
                return _Group(value, j, True, False)

        if self.at(j) == "hundred":
            value, j = (value or 1) * 100, j + 1
            k = j + 1 if self.at(j) == "and" and self.at(j + 1) in NUMBER_WORDS else j
            tail, k2 = self.tens_tail(k)
            if tail:
                value, j = value + tail, k2
        return _Group(value, j, False, False) if j > i else None

    def amount_at(self, i):
        """The longest amount starting at token i, or None."""
        total, j, strength, prev_scale = 0.0, i, 0, None
        while True:
            g = self.group(j)
            if not g:
                break
            scale_tok = self.at(g.end)
            if scale_tok in SCALES and not g.terminal:
                scale = SCALES[scale_tok]
                if prev_scale is not None and scale >= prev_scale:
                    break
                total += g.value * scale
                prev_scale, j, strength = scale, g.end + 1, 2
                if scale_tok in TERMINAL_SCALES:
                    break
                if self.at(j) == "and" and self.at(j + 1) in NUMBER_WORDS:
                    j += 1
                continue
            if prev_scale is not None and g.value >= prev_scale:
                break
            total += g.value
            j, strength = g.end, max(strength, int(g.shorthand))
            break
        if j == i:
            return None
        if self.at(j) == "naira":
            j, strength = j + 1, 2
        span = self.t[i:j]
        if not strength and (len(span) < 2 or not any(
                w == "hundred" or w in TENS or w in TEENS for w in span)):
            return None
        total = round(total, 6)
        if total <= 0 or total != int(total):
            return None
        return Amount(int(total), i, j, strength)


def find_amounts(text):
    """Every amount in the transcript, left to right."""
    tokens = tokenize(text)
    p, found, i = _Parser(tokens), [], 0
    while i < len(tokens):
        if tokens[i] in DIGIT_WORDS and p.digit_run(i) >= 3:
            i += p.digit_run(i)                          # step over account numbers whole
            continue
        if p.chunk_run(i) >= 3:
            i += p.chunk_run(i)
            continue
        a = p.amount_at(i)
        if a:
            found.append(a)
            i = a.end
        else:
            i += 1
    return found


def parse_amount(text):
    """The amount the speaker meant, in whole naira, or None if none was spoken."""
    found = find_amounts(text)
    if not found:
        return None
    best = max(a.strength for a in found)
    return [a for a in found if a.strength == best][-1].value
