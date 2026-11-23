"""Numbers in words for the speech engine (Italian), digits kept everywhere else."""

from __future__ import annotations

import pytest

from evokai.audio.speech.numbers import it_cardinal, spell_numbers
from evokai.i18n import speech_rules


@pytest.mark.parametrize(
    ("n", "words"),
    [
        (0, "zero"),
        (1, "uno"),
        (3, "tre"),
        (10, "dieci"),
        (11, "undici"),
        (16, "sedici"),
        (17, "diciassette"),
        (19, "diciannove"),
        (20, "venti"),
        (21, "ventuno"),
        (23, "ventitré"),
        (28, "ventotto"),
        (31, "trentuno"),
        (33, "trentatré"),
        (38, "trentotto"),
        (45, "quarantacinque"),
        (99, "novantanove"),
        (100, "cento"),
        (101, "centouno"),
        (103, "centotré"),
        (108, "centotto"),
        (111, "centoundici"),
        (180, "centottanta"),
        (200, "duecento"),
        (288, "duecentottantotto"),
        (999, "novecentonovantanove"),
        (1000, "mille"),
        (1001, "milleuno"),
        (1003, "milletré"),
        (1008, "milleotto"),
        (1100, "millecento"),
        (1234, "milleduecentotrentaquattro"),
        (2000, "duemila"),
        (2026, "duemilaventisei"),
        (3000, "tremila"),
        (12_345, "dodicimilatrecentoquarantacinque"),
        (21_000, "ventunomila"),
        (23_000, "ventitremila"),
        (100_000, "centomila"),
        (101_000, "centounomila"),
        (999_999, "novecentonovantanovemilanovecentonovantanove"),
        (1_000_000, "un milione"),
        (1_000_003, "un milione tre"),
        (1_200_000, "un milione duecentomila"),
        (1_234_567, "un milione duecentotrentaquattromilacinquecentosessantasette"),
        (2_000_000, "due milioni"),
        (23_000_000, "ventitré milioni"),
        (100_000_000, "cento milioni"),
        (1_000_000_000, "un miliardo"),
        (2_500_000_000, "due miliardi cinquecento milioni"),
        (
            999_999_999_999,
            "novecentonovantanove miliardi novecentonovantanove milioni "
            "novecentonovantanovemilanovecentonovantanove",
        ),
    ],
)
def test_italian_cardinals(n: int, words: str) -> None:
    assert it_cardinal(n) == words


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        # integers
        ("1.000", "mille"),
        ("12.345", "dodicimilatrecentoquarantacinque"),
        ("1.234.567", "un milione duecentotrentaquattromilacinquecentosessantasette"),
        ("999.999.999.999", it_cardinal(999_999_999_999)),
        ("2026", "duemilaventisei"),
        ("15000", "quindicimila"),
        ("0", "zero"),
        # decimals
        ("12,5", "dodici virgola cinque"),
        ("0,05", "zero virgola zero cinque"),
        ("3,14", "tre virgola quattordici"),
        ("1,000", "uno virgola zero zero zero"),
        ("2,71828", "due virgola sette uno otto due otto"),
        ("1.234,5", "milleduecentotrentaquattro virgola cinque"),
        # percentages
        ("12,5%", "dodici virgola cinque per cento"),
        ("12,5 %", "dodici virgola cinque per cento"),
        ("100%", "cento per cento"),
        # euros
        ("1.234,56 €", "milleduecentotrentaquattro euro e cinquantasei"),
        ("€ 1.234,56", "milleduecentotrentaquattro euro e cinquantasei"),
        ("€1.234,56", "milleduecentotrentaquattro euro e cinquantasei"),
        ("1.234,56 euro", "milleduecentotrentaquattro euro e cinquantasei"),
        ("1.234,56 EUR", "milleduecentotrentaquattro euro e cinquantasei"),
        ("12 €", "dodici euro"),
        ("12€", "dodici euro"),
        ("1 €", "un euro"),
        ("1 euro", "un euro"),
        ("1,05 €", "un euro e cinque"),
        ("1,5 €", "un euro e cinquanta"),
        ("12,00 €", "dodici euro"),
        ("0,50 €", "cinquanta centesimi"),
        ("0,01 €", "un centesimo"),
        ("1.000.000 €", "un milione di euro"),
        ("2.000.000 euro", "due milioni di euro"),
        ("2.500.000 €", "due milioni cinquecentomila euro"),
        # dates and times
        ("16/11/2026", "sedici novembre duemilaventisei"),
        ("1/3/2026", "primo marzo duemilaventisei"),
        ("01/01/2000", "primo gennaio duemila"),
        ("31/12/99", "trentuno dicembre millenovecentonovantanove"),
        ("17:26", "diciassette e ventisei"),
        ("9:05", "nove e cinque"),
        ("12:00", "dodici"),
        ("0:30", "zero e trenta"),
        # signs
        ("-3,5%", "meno tre virgola cinque per cento"),
        ("−3,5%", "meno tre virgola cinque per cento"),
        ("+2%", "più due per cento"),
        ("-1.234,56 €", "meno milleduecentotrentaquattro euro e cinquantasei"),
        ("(-5)", "(meno cinque)"),
        # ordinals and degrees
        ("1°", "primo"),
        ("3°", "terzo"),
        ("10°", "decimo"),
        ("2ª", "seconda"),
        ("11º", "undicesimo"),
        ("23ª", "ventitreesima"),
        ("26º", "ventiseiesimo"),
        ("25°C", "venticinque gradi"),
        ("25 °C", "venticinque gradi"),
        ("25°", "venticinque gradi"),
    ],
)
def test_italian_spoken_forms(text: str, spoken: str) -> None:
    assert spell_numbers(text, "it") == spoken


@pytest.mark.parametrize(
    "text",
    [
        "COVID-19",
        "v2",
        "MP3",
        "3D",
        "5G",
        "abc-5",
        "2.0.1",
        "1.5",
        "3:1",
        "1/2",
        "17:26:30",
        "45/12/2026",
        "16/13/2026",
        "25:61",
        "00184",
        "007",
        "1.000.000.000.000",
        "123456789012345",
        "1.00",
        "x_12",
        "nessun numero qui",
    ],
)
def test_italian_leaves_codes_versions_and_identifiers(text: str) -> None:
    assert spell_numbers(text, "it") == text


def test_a_whole_italian_sentence() -> None:
    text = (
        "Il 1° gennaio 2026 alle 9:30 il saldo era di -1.234,56 €, cioè il 12,5% in meno "
        "rispetto ai 1.400 € del 16/11/2025; la versione 2.0.1 di COVID-19 non c'entra."
    )
    assert spell_numbers(text, "it") == (
        "Il primo gennaio duemilaventisei alle nove e trenta il saldo era di meno "
        "milleduecentotrentaquattro euro e cinquantasei, cioè il dodici virgola cinque per cento "
        "in meno rispetto ai millequattrocento euro del sedici novembre duemilaventicinque; la "
        "versione 2.0.1 di COVID-19 non c'entra."
    )


def test_punctuation_around_numbers_stays() -> None:
    assert spell_numbers("Sono 1.000 punti.", "it") == "Sono mille punti."
    assert (
        spell_numbers("Totale: 3, poi 4; infine 5!", "it")
        == "Totale: tre, poi quattro; infine cinque!"
    )
    assert spell_numbers("«12,5%»", "it") == "«dodici virgola cinque per cento»"


def test_other_languages_are_spoken_as_written() -> None:
    assert spell_numbers("1.000 points, 12.5%", "en") == "1.000 points, 12.5%"
    assert spell_numbers("1.000", "xx") == "1.000"
    assert spell_numbers("1.000", "it-IT") == "mille"


def test_speech_rules_carry_their_locale() -> None:
    assert speech_rules("it").locale == "it"
    assert speech_rules().locale == "en"


@pytest.mark.parametrize(
    ("locale", "text", "spoken"),
    [
        # Spanish: dot groups, comma decimals, "de" in dates
        ("es", "1.000", "mil"),
        ("es", "12,5%", "doce coma cinco por ciento"),
        ("es", "1.234,56 €", "mil doscientos treinta y cuatro euros con cincuenta y seis"),
        ("es", "1 €", "un euro"),
        ("es", "0,01 €", "un céntimo"),
        ("es", "1.000.000 €", "un millón de euros"),
        ("es", "16/11/2026", "dieciséis de noviembre de dos mil veintiséis"),
        ("es", "17:26", "diecisiete y veintiséis"),
        ("es", "1º", "primero"),
        ("es", "2ª", "segunda"),
        ("es", "-3", "menos tres"),
        # French: narrow no-break space groups, comma decimals
        ("fr", "1\u202f000", "mille"),
        ("fr", "1\u00a0234,5", "mille deux cent trente-quatre virgule cinq"),
        ("fr", "12,5 %", "douze virgule cinq pour cent"),
        ("fr", "2 €", "deux euros"),
        ("fr", "1/3/2026", "premier mars deux mille vingt-six"),
        ("fr", "17:26", "dix-sept heures vingt-six"),
        ("fr", "9:00", "neuf heures"),
        ("fr", "25°C", "vingt-cinq degrés"),
        # German: dot groups and dot dates
        ("de", "1.000", "eintausend"),
        ("de", "12,5%", "zwölf Komma fünf Prozent"),
        ("de", "16.11.2026", "sechzehnte November zweitausendsechsundzwanzig"),
        ("de", "17:26", "siebzehn Uhr sechsundzwanzig"),
        ("de", "3 €", "drei Euro"),
        # Romanian
        ("ro", "1.000", "o mie"),
        ("ro", "12,5%", "doisprezece virgulă cinci la sută"),
        ("ro", "16.11.2026", "șaisprezece noiembrie două mii douăzeci și șase"),
        # Swedish: space groups, comma decimals
        ("sv", "1\u00a0000", "etttusen"),
        ("sv", "12,5 %", "tolv komma fem procent"),
        ("sv", "17:26", "sjutton och tjugosex"),
        ("sv", "-2", "minus två"),
    ],
)
def test_other_languages_spell_numbers(locale: str, text: str, spoken: str) -> None:
    assert spell_numbers(text, locale) == spoken


@pytest.mark.parametrize("locale", ["es", "fr", "de", "ro", "sv"])
def test_other_languages_leave_codes_and_versions(locale: str) -> None:
    for text in ("COVID-19", "v2", "2.0.1", "00184", "123456789012345"):
        assert spell_numbers(text, locale) == text
