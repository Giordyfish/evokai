"""Numbers in words, for the speech engine only.

A speech engine reads «1.000» as «uno punto zero zero zero»: it does not know the language's
separators. In voice mode the text handed to the engine has its numbers written out in words
(«mille»), while what is shown (the chat, the subtitles) keeps the readable digits.

One speller per language; a language without one is spoken as written. Italian covers integers
(with or without thousand dots), decimals, percentages, euros, dates (dd/mm/yyyy), times (hh:mm),
signs and ordinals (1°, 1ª). Digits that touch letters (identifiers, «COVID-19», «v2») and strings
that fit no format (versions like «2.0.1», codes with a leading zero, runs longer than twelve
digits) are left as they are.
"""

from __future__ import annotations

import re
from collections.abc import Callable

# A number with its sign, currency and suffix. The guards keep digits inside words and identifiers
# untouched: no letter, digit or underscore before, no separator before (the tail of a longer
# string), no letter-hyphen before («COVID-19»); no digit or separator-digit after the number, no
# letter after the whole match.
_TOKEN = re.compile(
    r"(?<!\w)(?<![.,/:])(?<![^\W\d]-)"
    r"(?:(?<![^\s(\[«\"'])(?P<sign>[-−+]))?"
    r"(?P<pre>(?:€|\bEUR)\s?)?"
    r"(?P<num>\d+(?:[.,:/]\d+)*)(?!\d)(?![.,:/]\d)"
    r"(?P<suf>\s?%|\s?€|\s(?:euro|EUR)\b|\s?°\s?[CF]\b|[°ºª])?"
    r"(?!\w)"
)

Speller = Callable[[str], str]


def spell_numbers(text: str, locale: str) -> str:
    """Write the numbers of a text in words, the way the language reads them.

    Args:
        text: Speakable text (already plain, identifiers already redacted).
        locale: The language, ``it`` or ``it-IT``; a language without a speller is unchanged.

    Returns:
        The text for the speech engine.
    """
    speller = _SPELLERS.get(locale.split("-")[0].lower())
    if speller is None or not any(ch.isdigit() for ch in text):
        return text
    return speller(text)


# ── Italian ─────────────────────────────────────────────────────────────────────────────────────

_IT_UNITS = (
    "zero uno due tre quattro cinque sei sette otto nove dieci undici dodici tredici quattordici "
    "quindici sedici diciassette diciotto diciannove"
).split()
_IT_TENS = "_ _ venti trenta quaranta cinquanta sessanta settanta ottanta novanta".split()
_IT_MONTHS = (
    "gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre"
).split()
_IT_ORDINALS = "primo secondo terzo quarto quinto sesto settimo ottavo nono decimo".split()

_IT_MAX = 999_999_999_999
_GROUPED = re.compile(r"[1-9]\d{0,2}(?:\.\d{3})+")
_PLAIN = re.compile(r"0|[1-9]\d{0,11}")
_TIME = re.compile(r"(\d{1,2}):(\d{2})")
_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})")


def _it_below_hundred(n: int) -> str:
    if n < 20:
        return _IT_UNITS[n] if n else ""
    tens, unit = divmod(n, 10)
    word = _IT_TENS[tens]
    if unit in (1, 8):  # ventuno, ventotto
        word = word[:-1]
    return word + (_IT_UNITS[unit] if unit else "")


def _it_below_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    head = "" if not hundreds else "cento" if hundreds == 1 else _IT_UNITS[hundreds] + "cento"
    tail = _it_below_hundred(rest)
    if head and tail.startswith("o"):  # centotto, centottanta
        head = head[:-1]
    return head + tail


def _it_below_million(n: int) -> str:
    thousands, rest = divmod(n, 1000)
    if not thousands:
        head = ""
    elif thousands == 1:
        head = "mille"
    else:
        head = _it_below_thousand(thousands) + "mila"
    return head + _it_below_thousand(rest)


def _it_accent(word: str, n: int) -> str:
    # Ventitré, centotré, milletré: a compound ending in «tre» takes the accent.
    return word[:-3] + "tré" if n > 3 and word.endswith("tre") else word


def it_cardinal(n: int) -> str:
    """An Italian cardinal number in words (0 to 999.999.999.999).

    Returns:
        «mille», «duemilaventisei», «un milione duecentomila».
    """
    if n == 0:
        return "zero"
    billions, rest = divmod(n, 1_000_000_000)
    millions, rest = divmod(rest, 1_000_000)
    parts: list[str] = []
    for count, one, many in (
        (billions, "un miliardo", "miliardi"),
        (millions, "un milione", "milioni"),
    ):
        if count == 1:
            parts.append(one)
        elif count:
            parts.append(f"{_it_accent(_it_below_million(count), count)} {many}")
    if rest:
        parts.append(_it_accent(_it_below_million(rest), rest))
    return " ".join(parts)


def _it_ordinal(n: int, feminine: bool) -> str:
    if n <= 10:
        word = _IT_ORDINALS[n - 1]
    else:
        word = it_cardinal(n).replace("tré", "tre")
        if word.endswith(("tre", "sei")):  # ventitreesimo, ventiseiesimo
            word += "esimo"
        else:
            word = word[:-1] + "esimo"
    return word[:-1] + "a" if feminine else word


def _it_digits(digits: str) -> str:
    return " ".join(_IT_UNITS[int(d)] for d in digits)


def _it_integer(digits: str) -> int | None:
    # The value of an integer part written «1.234.567» or «1234567», else None (a code).
    if _GROUPED.fullmatch(digits) or _PLAIN.fullmatch(digits):
        n = int(digits.replace(".", ""))
        return n if n <= _IT_MAX else None
    return None


def _it_fraction(digits: str) -> str:
    # Decimals: «5», «quattordici»; with a leading zero or longer than three, digit by digit.
    if digits.startswith("0") or len(digits) > 3:
        return _it_digits(digits)
    return it_cardinal(int(digits))


def _it_number(num: str) -> tuple[int, str | None] | None:
    whole, _, fraction = num.partition(",")
    if "," in fraction:
        return None
    n = _it_integer(whole)
    if n is None or (fraction and not fraction.isdigit()):
        return None
    return n, fraction or None


def _it_euro(n: int, fraction: str | None) -> str:
    cents = 0
    if fraction is not None:
        if len(fraction) > 2:
            return f"{it_cardinal(n)} virgola {_it_fraction(fraction)} euro"
        cents = int(fraction.ljust(2, "0"))
    if n == 0 and cents:
        return "un centesimo" if cents == 1 else f"{it_cardinal(cents)} centesimi"
    if n == 1:
        amount = "un euro"
    elif n and n % 1_000_000 == 0:
        amount = f"{it_cardinal(n)} di euro"  # un milione di euro
    else:
        amount = f"{it_cardinal(n)} euro"
    return f"{amount} e {it_cardinal(cents)}" if cents else amount


def _it_time(num: str) -> str | None:
    m = _TIME.fullmatch(num)
    if not m:
        return None
    hours, minutes = int(m.group(1)), int(m.group(2))
    if hours > 23 or minutes > 59:
        return None
    return it_cardinal(hours) + (f" e {it_cardinal(minutes)}" if minutes else "")


def _it_date(num: str) -> str | None:
    m = _DATE.fullmatch(num)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    if len(m.group(3)) == 2:
        year += 2000 if year < 50 else 1900
    first = "primo" if day == 1 else it_cardinal(day)
    return f"{first} {_IT_MONTHS[month - 1]} {it_cardinal(year)}"


def _it_token(m: re.Match[str]) -> str:
    sign, pre, num = m.group("sign"), m.group("pre"), m.group("num")
    suffix = (m.group("suf") or "").strip()
    euro = bool(pre) or suffix in ("€", "euro", "EUR")
    if not (sign or euro or suffix):
        said = _it_time(num) or _it_date(num)
        if said:
            return said
    parsed = _it_number(num)
    if parsed is None:
        return m.group(0)
    n, fraction = parsed
    if suffix in ("°", "º", "ª"):
        whole = n > 0 and fraction is None and not (sign or euro)
        # 1° posto, 2ª classe; past ten a «°» is degrees (25°), an «º» or «ª» still an ordinal.
        if whole and (suffix != "°" or n <= 10):
            return _it_ordinal(n, feminine=suffix == "ª")
        if suffix != "°":
            return m.group(0)
    if euro:
        said = _it_euro(n, fraction)
    else:
        said = it_cardinal(n) + (f" virgola {_it_fraction(fraction)}" if fraction else "")
        if suffix == "%":
            said += " per cento"
        elif suffix.startswith("°"):
            said += " gradi"
    if sign:
        said = ("più " if sign == "+" else "meno ") + said
    return said


def _spell_it(text: str) -> str:
    return _TOKEN.sub(_it_token, text)


_SPELLERS: dict[str, Speller] = {"it": _spell_it}
"""The languages whose numbers are spelled out, by language code."""
