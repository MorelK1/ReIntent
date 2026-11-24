#!/usr/bin/env python3
"""Tests du contrôle des valeurs écrites en toutes lettres (intent_store.py, campagne V3).

Lancer : python3 -I test_number_words.py   (aucune dépendance ; n'écrit aucun fichier)
"""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import intent_store as st  # noqa: E402

OK = [
    ("three hundred and eighty-five", "385"),
    ("three hundred eighty five", "385"),
    ("four hundred", "400"),
    ("five hundred and thirty", "530"),
    ("two hundred and ninety-two", "292"),
    ("four and a half", "4.5"),
    ("twelve and a half", "12.5"),
    ("five and a quarter", "5.25"),
    ("sixteen", "16"),
    ("three point five", "3.5"),
    ("zero point three eight five", "0.385"),
    ("four thousand five hundred", "4500"),
    ("eleven thousand", "11000"),
    ("a hundred and ten", "110"),
    ("thirteen thousand five hundred", "13500"),
]
BAD = [
    "five five", "twenty twelve", "hundred", "three hundred and", "four and a",
    "half four", "point five", "three point fifteen", "fifteen hundred", "thousand thousand",
    "two thousand three thousand", "and five", "zero five",
]


def test_parser():
    for words, val in OK:
        run, got = st.number_words_in_form(words + " gigabytes")
        assert got == Decimal(val), (words, got, val)
    for words in BAD:
        try:
            got = st.words_to_decimal(st.word_tokens(words))
        except ValueError:
            continue
        raise AssertionError(f"forme acceptée à tort : '{words}' -> {got}")


SCEN = {"id": "T-1", "requirements": [
    {"target_type": "service", "target_id": "C4", "kpi_type": "ram", "comparator": "gte",
     "baseline_value": 3.0, "new_value": 4.5, "unit": "GB", "component_name": "AR Rendering"},
    {"target_type": "flow", "target_id": "f1", "kpi_type": "e2e_latency", "comparator": "lte",
     "baseline_value": 500.0, "new_value": 385, "unit": "ms", "flow_name": "Object Recognition Flow"},
]}


def entry(text, ram_form, ram_val, lat_form, lat_val, ram_words=True, lat_words=False):
    m1 = {"target_id": "C4", "kpi_type": "ram", "comparator": "gte", "written_value": ram_val,
          "written_unit": "gigabytes", "written_form": ram_form, "designation": "name", "level": 2}
    if ram_words:
        m1["number_in_words"] = True
    m2 = {"target_id": "f1", "kpi_type": "e2e_latency", "comparator": "lte", "written_value": lat_val,
          "written_unit": "ms", "written_form": lat_form, "designation": "id", "level": 1}
    if lat_words:
        m2["number_in_words"] = True
    return {"scenario_id": "T-1", "intent": text, "mentions": [m1, m2], "baseline_mentions": [],
            "plan": {"style": "imperative", "register": "developer", "structure": "single_sentence"}}


def errs(e):
    return st.validate(e, SCEN, [])[0]


def test_validate():
    good = entry("Give AR Rendering at least four and a half gigabytes and keep f1 under 385 ms.",
                 "four and a half gigabytes", "4.5", "385 ms", "385")
    assert errs(good) == [], errs(good)
    # mots justes mais written_value faux et cohérent avec rien -> refus par check_value
    assert errs(entry("Give AR Rendering at least four and a half gigabytes and keep f1 under 385 ms.",
                      "four and a half gigabytes", "4.0", "385 ms", "385"))
    # written_value juste mais mots faux : c'est le cas que l'ancien validateur laissait passer
    bad = entry("Give AR Rendering at least four gigabytes and keep f1 under 385 ms.",
                "four gigabytes", "4.5", "385 ms", "385")
    assert any("se lit 4" in x for x in errs(bad)), errs(bad)
    # latence en lettres, exacte
    lat = entry("Give AR Rendering at least four and a half gigabytes and keep f1 under three hundred and "
                "eighty-five ms.", "four and a half gigabytes", "4.5", "three hundred and eighty-five ms", "385",
                lat_words=True)
    assert errs(lat) == [], errs(lat)
    lat_bad = entry("Give AR Rendering at least four and a half gigabytes and keep f1 under three hundred and "
                    "eighty ms.", "four and a half gigabytes", "4.5", "three hundred and eighty ms", "385",
                    lat_words=True)
    assert any("se lit 380" in x for x in errs(lat_bad)), errs(lat_bad)
    # chiffres dans une forme déclarée en lettres
    mixed = entry("Give AR Rendering at least 4 and a half gigabytes and keep f1 under 385 ms.",
                  "4 and a half gigabytes", "4.5", "385 ms", "385")
    assert any("contient des chiffres" in x for x in errs(mixed)), errs(mixed)
    # nombre en lettres non déclaré (comptage) -> refus
    extra = entry("Two teams asked: give AR Rendering at least four and a half gigabytes and keep f1 under 385 ms.",
                  "four and a half gigabytes", "4.5", "385 ms", "385")
    assert any("'two'" in x for x in errs(extra)), errs(extra)


if __name__ == "__main__":
    test_parser()
    test_validate()
    print("tests nombres en lettres : OK")
