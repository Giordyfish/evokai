"""Numbers in words, for the speech engine only.

A speech engine reads «1.000» as «uno punto zero zero zero»: it does not know the language's
separators. In voice mode the text handed to the engine has its numbers written out in words
(«mille»), while what is shown (the chat, the subtitles) keeps the readable digits.

The words come from `num2words <https://pypi.org/project/num2words/>`_; the pack of each language
(``speech.numbers`` in ``evokai/locales/<code>.json``) says how its numbers are written (group and
decimal separators, the date separator) and the words around them (decimal comma, per cent,
degrees, signs, the currency, the shape of a date and a time). A language whose pack has no
``numbers`` block (English) is spoken as written. Italian cardinals and ordinals keep their own
speller: num2words 0.5.14 writes 118 as «centodicotto» and drops the accent of «ventitré».

Every language covers integers (with or without group separators), decimals, percentages, euros,
dates, times (hh:mm), signs and, where the pack says so, ordinals (1°, 1ª). Digits that touch
letters (identifiers, «COVID-19», «v2») and strings that fit no format (versions like «2.0.1»,
codes with a leading zero, runs longer than twelve digits) are left as they are.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from typing import Any

from num2words import num2words

from evokai.i18n import known_locales, pack

# A number with its sign, currency and suffix. The guards keep digits inside words and identifiers
# untouched: no letter, digit or underscore before, no separator before (the tail of a longer
# string), no letter-hyphen before («COVID-19»); no digit or separator-digit after the number, no
# letter after the whole match. Groups may be split by a no-break space (French, Swedish).
_SEP = r"[.,:/\u00a0\u202f]"
_TOKEN = re.compile(
    r"(?<!\w)(?<![.,/:\u00a0\u202f])(?<![^\W\d]-)"
    r"(?:(?<![^\s(\[«\"'])(?P<sign>[-−+]))?"
    r"(?P<pre>(?:€|\bEUR)\s?)?"
    rf"(?P<num>\d+(?:{_SEP}\d+)*)(?!\d)(?!{_SEP}\d)"
    r"(?P<suf>\s?%|\s?€|\s(?:euro|EUR)\b|\s?°\s?[CF]\b|[°ºª])?"
    r"(?!\w)"
)

_MAX = 999_999_999_999
_PLAIN = re.compile(r"0|[1-9]\d{0,11}")
_TIME = re.compile(r"(\d{1,2}):(\d{2})")


@dataclass(frozen=True)
class NumberWords:
    """How one language writes and says its numbers (the pack's ``speech.numbers``)."""

    lang: str
    """The num2words language."""
    group: tuple[str, ...]
    decimal: str
    decimal_word: str
    percent: str
    degrees: str
    plus: str
    minus: str
    euro_one: str
    euro_many: str
    euro_millions: str
    cent_one: str
    cent_many: str
    and_: str
    date_sep: str
    date_format: str
    first_day: str | None
    day_ordinal: bool
    months: tuple[str, ...]
    time_format: str
    hour_format: str
    ordinals: bool
    feminine: tuple[str, str] | None


@cache
def number_words(locale: str) -> NumberWords | None:
    """The number words of a language, or None when its pack has none.

    Returns:
        The words.
    """
    language = locale.split("-")[0].lower()
    if language not in known_locales():
        return None
    p = pack(language)
    n: dict[str, Any] | None = p["speech"].get("numbers")
    if not n:
        return None
    cur, date, time = n["currency"], n["date"], n["time"]
    fem = n.get("feminine")
    return NumberWords(
        lang=n.get("lang", language),
        group=tuple(n["group"]),
        decimal=n["decimal"],
        decimal_word=n["decimalWord"],
        percent=n["percent"],
        degrees=n["degrees"],
        plus=n["plus"],
        minus=n["minus"],
        euro_one=cur["one"],
        euro_many=cur["many"],
        euro_millions=cur.get("millions") or cur["many"],
        cent_one=cur["centOne"],
        cent_many=cur["centMany"],
        and_=cur["and"],
        date_sep=date["sep"],
        date_format=date["format"],
        first_day=date.get("first"),
        day_ordinal=bool(date.get("ordinal")),
        months=tuple(p["calendar"]["months"]),
        time_format=time["format"],
        hour_format=time["hour"],
        ordinals=bool(n.get("ordinals")),
        feminine=(str(fem[0]), str(fem[1])) if fem else None,
    )


def spell_numbers(text: str, locale: str) -> str:
    """Write the numbers of a text in words, the way the language reads them.

    Args:
        text: Speakable text (already plain, identifiers already redacted).
        locale: The language, ``it`` or ``it-IT``; a language without number words is unchanged.

    Returns:
        The text for the speech engine.
    """
    if not any(ch.isdigit() for ch in text):
        return text
    words = number_words(locale)
    if words is None:
        return text
    return _TOKEN.sub(lambda m: _token(m, words), text)


def cardinal(n: int, w: NumberWords) -> str:
    """A cardinal number in words.

    Returns:
        The words.
    """
    return it_cardinal(n) if w.lang == "it" else str(num2words(n, lang=w.lang))


def ordinal(n: int, w: NumberWords, *, feminine: bool = False) -> str:
    """An ordinal number in words (masculine unless asked; feminine by the pack's rule).

    Returns:
        The words.
    """
    word = _it_ordinal(n) if w.lang == "it" else str(num2words(n, lang=w.lang, to="ordinal"))
    if feminine and w.feminine and word.endswith(w.feminine[0]):
        word = word[: -len(w.feminine[0])] + w.feminine[1]
    return word


# ── Italian cardinals and ordinals ──────────────────────────────────────────────────────────────

_IT_UNITS = (
    "zero uno due tre quattro cinque sei sette otto nove dieci undici dodici tredici quattordici "
    "quindici sedici diciassette diciotto diciannove"
).split()
_IT_TENS = "_ _ venti trenta quaranta cinquanta sessanta settanta ottanta novanta".split()
_IT_ORDINALS = "primo secondo terzo quarto quinto sesto settimo ottavo nono decimo".split()


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


def _it_ordinal(n: int) -> str:
    if n <= 10:
        return _IT_ORDINALS[n - 1]
    word = it_cardinal(n).replace("tré", "tre")
    if word.endswith(("tre", "sei")):  # ventitreesimo, ventiseiesimo
        return word + "esimo"
    return word[:-1] + "esimo"


# ── the shapes every language shares ────────────────────────────────────────────────────────────


def _digits(digits: str, w: NumberWords) -> str:
    return " ".join(cardinal(int(d), w) for d in digits)


def _integer(digits: str, w: NumberWords) -> int | None:
    # The value of an integer part written with the language's groups or plain, else None (a code).
    for g in w.group:
        if re.fullmatch(rf"[1-9]\d{{0,2}}(?:{re.escape(g)}\d{{3}})+", digits):
            n = int(digits.replace(g, ""))
            return n if n <= _MAX else None
    if _PLAIN.fullmatch(digits):
        return int(digits)
    return None


def _fraction(digits: str, w: NumberWords) -> str:
    # Decimals: «5», «quattordici»; with a leading zero or longer than three, digit by digit.
    if digits.startswith("0") or len(digits) > 3:
        return _digits(digits, w)
    return cardinal(int(digits), w)


def _number(num: str, w: NumberWords) -> tuple[int, str | None] | None:
    whole, _, fraction = num.partition(w.decimal)
    if w.decimal in fraction:
        return None
    n = _integer(whole, w)
    if n is None or (fraction and not fraction.isdigit()):
        return None
    return n, fraction or None


def _euro(n: int, fraction: str | None, w: NumberWords) -> str:
    cents = 0
    if fraction is not None:
        if len(fraction) > 2:
            return f"{cardinal(n, w)} {w.decimal_word} {_fraction(fraction, w)} {w.euro_many}"
        cents = int(fraction.ljust(2, "0"))
    if n == 0 and cents:
        return w.cent_one if cents == 1 else f"{cardinal(cents, w)} {w.cent_many}"
    if n == 1:
        amount = w.euro_one
    elif n and n % 1_000_000 == 0:
        amount = f"{cardinal(n, w)} {w.euro_millions}"  # un milione di euro
    else:
        amount = f"{cardinal(n, w)} {w.euro_many}"
    return f"{amount} {w.and_} {cardinal(cents, w)}" if cents else amount


def _time(num: str, w: NumberWords) -> str | None:
    m = _TIME.fullmatch(num)
    if not m:
        return None
    hours, minutes = int(m.group(1)), int(m.group(2))
    if hours > 23 or minutes > 59:
        return None
    h = cardinal(hours, w)
    if not minutes:
        return w.hour_format.format(h=h)
    return w.time_format.format(h=h, m=cardinal(minutes, w))


def _date(num: str, w: NumberWords) -> str | None:
    sep = re.escape(w.date_sep)
    m = re.fullmatch(rf"(\d{{1,2}}){sep}(\d{{1,2}}){sep}(\d{{4}}|\d{{2}})", num)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    if len(m.group(3)) == 2:
        year += 2000 if year < 50 else 1900
    if day == 1 and w.first_day:
        said_day = w.first_day
    elif w.day_ordinal:
        said_day = ordinal(day, w)
    else:
        said_day = cardinal(day, w)
    return w.date_format.format(day=said_day, month=w.months[month - 1], year=cardinal(year, w))


def _token(m: re.Match[str], w: NumberWords) -> str:
    sign, pre, num = m.group("sign"), m.group("pre"), m.group("num")
    suffix = (m.group("suf") or "").strip()
    euro = bool(pre) or suffix in ("€", "euro", "EUR")
    if not (sign or euro or suffix):
        said = _time(num, w) or _date(num, w)
        if said:
            return said
    parsed = _number(num, w)
    if parsed is None:
        return m.group(0)
    n, fraction = parsed
    if suffix in ("°", "º", "ª"):
        whole = n > 0 and fraction is None and not (sign or euro)
        # 1° posto, 2ª classe; past ten a «°» is degrees (25°), an «º» or «ª» still an ordinal.
        if w.ordinals and whole and (suffix != "°" or n <= 10):
            return ordinal(n, w, feminine=suffix == "ª")
        if suffix != "°":
            return m.group(0)
    if euro:
        said = _euro(n, fraction, w)
    else:
        said = cardinal(n, w) + (f" {w.decimal_word} {_fraction(fraction, w)}" if fraction else "")
        if suffix == "%":
            said += f" {w.percent}"
        elif suffix.startswith("°"):
            said += f" {w.degrees}"
    if sign:
        said = f"{w.plus if sign == '+' else w.minus} {said}"
    return said
