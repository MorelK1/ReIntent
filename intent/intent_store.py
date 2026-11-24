#!/usr/bin/env python3
"""Stockage et contrôle des intentions ARVR-150. Ne génère AUCUN texte.

Commandes :
  status                 entrées déjà enregistrées et prochain scénario à traiter
  revise LOT.json RAISON corrige une entrée déjà enregistrée (revalidée, ancien texte conservé)
  carry                  (jeu 500) reporte les intentions du jeu 150 vers leurs nouveaux ids, après revalidation
  view [next|ID] [N]     affiche N scénarios (exigences seulement, vérité terrain masquée)
  append LOT.json        contrôle puis enregistre, scénario par scénario, dans intents.json
  report                 répartition des niveaux/styles/longueurs/ouvertures déjà enregistrés
  blind-export OUT [A B]  exporte texte + expressions chiffrées (sans cible) pour la vérification sémantique
  audit                  revalide toutes les entrées enregistrées et écrit validation_report_<jeu>.json

Jeu de scénarios : INTENT_SET=v3 (défaut). Le chemin des scénarios peut être forcé par
INTENT_SCENARIOS=/chemin/scenarios.json. Sortie par défaut : intents.json.

Les textes sont écrits à la main (voir SKILL.md) ; ce script les vérifie et les enregistre.
"""

import json
import os
import re
import sys
import tempfile
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent
# Jeu de scénarios : INTENT_SET=v3 (défaut, le seul jeu que ce repo garde). Un autre jeu passé via
# INTENT_SET garde l'ancienne convention de nommage suffixé (intents_<jeu>.json, etc.), au cas où.
SET = os.environ.get("INTENT_SET", "v3")
SCENARIOS = Path(os.environ["INTENT_SCENARIOS"]) if os.environ.get("INTENT_SCENARIOS") else (
    ROOT.parent / "eval" / "benchmark" / "scenarios.json")
OUT = ROOT / ("intents.json" if SET == "v3" else f"intents_{SET}.json")
REPORT = ROOT / ("validation_report.json" if SET == "v3" else f"validation_report_{SET}.json")
SEMANTIC = ROOT / ("semantic_check.json" if SET == "v3" else f"semantic_check_{SET}.json")   # résolutions à l'aveugle (voir blind-export)

REVIEW_STATUS = "llm_generated_auto_checked"

UNITS = {
    "cpu": {
        "vcpu": "1", "vcpus": "1", "virtual cpu": "1", "virtual cpus": "1",
        "virtual core": "1", "virtual cores": "1",
        "mcpu": "0.001", "millicore": "0.001", "millicores": "0.001",
    },
    "ram": {
        "gb": "1", "gigabyte": "1", "gigabytes": "1",
        "mb": "0.001", "megabyte": "0.001", "megabytes": "0.001",
        "tb": "1000", "terabyte": "1000", "terabytes": "1000",
    },
    "e2e_latency": {
        "ms": "1", "millisecond": "1", "milliseconds": "1",
        "s": "1000", "sec": "1000", "second": "1000", "seconds": "1000",
        "µs": "0.001", "us": "0.001", "microsecond": "0.001", "microseconds": "0.001",
    },
}

DESIGNATIONS = {"id", "id+name", "name", "role", "flow", "context"}
STYLES = {"imperative", "polite_request", "need_statement", "constraint_statement",
          "goal_oriented", "narrative", "ticket_note", "question"}
REGISTERS = {"ops_terse", "developer", "manager", "formal", "casual", "product_owner"}
STRUCTURES = {"single_sentence", "two_sentences", "conditional", "enumeration", "clause_chain"}

FORBIDDEN = [
    (r"\bN\d{1,2}\b", "identifiant de noeud"),
    (r"\b(offload\w*|migrat\w*|relocat\w*|placement\w*|reposition\w*)", "mot de placement"),
    (r"\b(mov(e|es|ed|ing)|place[sd]?|placing|host(s|ed|ing)?)\b", "mot de placement/hébergement"),
    (r"\bnodes?\b", "mot 'node'"),
    (r"\b(edge|cloud|fog|iot)\b", "couche du continuum"),
    (r"\b(availab\w*|reliab\w*|uptime)\b", "disponibilité/fiabilité"),
    (r"\b(feasib\w*|infeasib\w*|impossible)\b", "mot de faisabilité"),
    (r"\b(about|roughly|approximately|around|approx\.?)\b", "mot d'approximation"),
    (r"~", "tilde d'approximation"),
    (r"\d,\d{3}", "séparateur de milliers"),
    (r"\bmips\b", "MIPS"),
]

UP_WORDS = r"\b(increas\w*|rais(e|es|ed|ing)|bump\w*|boost\w*)\b"
DOWN_WORDS = r"\b(tighten\w*|reduc\w*|lower\w*|shorten\w*|stricter|squeez\w*|trim\w*)\b"
RELAX_WORDS = r"\b(relax\w*|loosen\w*|extend\w*|more lenient)\b"
# Contrôle par exigence (V3) : chaque marqueur de sens est rattaché à l'exigence la plus proche dans la même
# phrase. Les adjectifs comparatifs disent aussi un sens (« faster » = resserrement, « slower » = relâchement).
DIR_MARKERS = [
    ("up", UP_WORDS),
    ("down", DOWN_WORDS),
    ("down", r"\b(faster|quicker|snappier|tighter)\b"),
    ("relax", RELAX_WORDS),
    ("relax", r"\b(slower|looser|leeway|more slack|extra time)\b"),
]

# ---------------------------------------------------------------- désignations (V3)
# Indices lexicaux tirés des noms et descriptions officiels de services.json / flows.json. Ils servent de
# FILTRE heuristique : leur absence ou leur mélange signale une désignation à faire trancher par la
# vérification sémantique indépendante (semantic_check_<jeu>.json) ; ils ne prouvent jamais l'univocité.
CUES = {
    "C1": r"captur|camera",
    "C2": r"recogni|detect|identif|\bobjects?\b",
    "C3": r"\bscene|context",
    "C4": r"render|overlay|augmented|\bAR\b|\bdraw",
    "C5": r"display|screen",
}
FLOW_CUES = {"f1": CUES["C2"], "f2": CUES["C4"]}
NAMES = {"C1": "Vision Capture", "C2": "Object Recognition", "C3": "Scene Processing", "C4": "AR Rendering",
         "C5": "Display Output", "f1": "Object Recognition Flow", "f2": "AR Rendering Flow"}
GENERIC_FLOW = r"\b(video (path|pipeline|stream)|main (pipeline|path|chain|flow)|the whole app)\b"

ID_ANY = r"(?<![A-Za-z0-9])(?:[Cc][1-5]|[Ff][12])(?![A-Za-z0-9])"
NUMBER = r"\d+(?:\.\d+)?"

# ---------------------------------------------------------------- nombres en toutes lettres (anglais)
# Grammaire admise : entiers jusqu'à 999 999 (« three hundred and eighty-five », « four thousand five
# hundred »), décimales par « point » chiffre à chiffre (« zero point three eight five »), fractions
# finales « and a half », « and a quarter », « and three quarters ». Toute autre forme est refusée.
W_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
W_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
W_SCALES = {"hundred": 100, "thousand": 1000}
W_CORE = set(W_UNITS) | set(W_TENS) | set(W_SCALES)
W_VOCAB = W_CORE | {"and", "a", "point", "half", "quarter", "quarters"}
FRACTIONS = [(["and", "a", "half"], "0.5"), (["and", "one", "half"], "0.5"),
             (["and", "a", "quarter"], "0.25"), (["and", "one", "quarter"], "0.25"),
             (["and", "three", "quarters"], "0.75")]
# mots de nombre interdits hors valeurs déclarées (règle 9) ; « one », « half »… sont souvent pronominaux
W_FORBIDDEN = (W_CORE - {"one", "zero"}) | {"million", "billion", "dozen", "dozens"}
W_SUSPECT = {"one", "zero", "half", "quarter", "quarters", "couple", "pair", "twice", "thrice"}


def word_tokens(text):
    return re.findall(r"[a-z]+", text.lower().replace("-", " "))


def _parse_int(toks):
    if not toks:
        raise ValueError("partie entière vide")
    total, cur, last = 0, 0, None
    for i, t in enumerate(toks):
        if t == "and":
            if last not in ("hundred", "thousand") or i == len(toks) - 1:
                raise ValueError("'and' mal placé")
            continue
        if t == "a":
            if i != 0 or len(toks) < 2 or toks[1] not in W_SCALES:
                raise ValueError("'a' mal placé")
            cur, last = 1, "unit"
            continue
        if t in W_UNITS:
            v = W_UNITS[t]
            if last in ("unit", "teen") or (last == "tens" and (v == 0 or v >= 10)):
                raise ValueError(f"'{t}' mal placé")
            if v == 0 and len(toks) > 1:
                raise ValueError("'zero' dans un entier composé")
            cur += v
            last = "unit" if v < 10 else "teen"
        elif t in W_TENS:
            if last in ("unit", "teen", "tens"):
                raise ValueError(f"'{t}' mal placé")
            cur += W_TENS[t]
            last = "tens"
        elif t == "hundred":
            if last != "unit" or not 1 <= cur <= 9:
                raise ValueError("'hundred' mal placé")
            cur *= 100
            last = "hundred"
        elif t == "thousand":
            if last is None or last == "thousand" or cur == 0 or total:
                raise ValueError("'thousand' mal placé")
            total, cur, last = cur * 1000, 0, "thousand"
        else:
            raise ValueError(f"mot '{t}' non numérique")
    return total + cur


def words_to_decimal(toks):
    """Convertit une suite de mots anglais en Decimal exact ; ValueError si la forme n'est pas admise."""
    toks = list(toks)
    if "point" in toks:
        i = toks.index("point")
        digits = toks[i + 1:]
        if not digits or any(W_UNITS.get(d, 10) > 9 for d in digits):
            raise ValueError("après 'point', seuls des chiffres isolés (zero..nine) sont admis")
        return Decimal(f"{_parse_int(toks[:i])}." + "".join(str(W_UNITS[d]) for d in digits))
    frac = Decimal(0)
    for suffix, val in FRACTIONS:
        if len(toks) > len(suffix) and toks[-len(suffix):] == suffix:
            frac, toks = Decimal(val), toks[:-len(suffix)]
            break
    if any(t in ("half", "quarter", "quarters", "point") for t in toks):
        raise ValueError("fraction mal placée")
    return Decimal(_parse_int(toks)) + frac


def number_words_in_form(form):
    """Repère l'unique suite de mots numériques de written_form et renvoie (tokens, Decimal)."""
    toks = word_tokens(form)
    runs, cur = [], []
    for t in toks + [None]:
        if t is not None and t in W_VOCAB:
            cur.append(t)
        else:
            if any(x in W_CORE for x in cur):
                runs.append(cur)
            cur = []
    if len(runs) != 1:
        raise ValueError(f"{len(runs)} suites de mots numériques dans written_form (une seule attendue)")
    run = runs[0]
    while run and run[0] in ("and",) or (run and run[0] == "a" and (len(run) < 2 or run[1] not in W_SCALES)):
        run = run[1:]
    return run, words_to_decimal(run)


# ---------------------------------------------------------------- données
def load_scenarios():
    with open(SCENARIOS, encoding="utf-8") as f:
        return json.load(f)


def load_out():
    if not OUT.exists():
        return []
    with open(OUT, encoding="utf-8") as f:
        return json.load(f)


def save_out(entries):
    order = {s["id"]: i for i, s in enumerate(load_scenarios())}
    entries = sorted(entries, key=lambda e: order[e["scenario_id"]])
    fd, tmp = tempfile.mkstemp(prefix="intents.", suffix=".tmp", dir=str(ROOT))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, OUT)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def fmt(x):
    return format(Decimal(repr(x)).normalize(), "f")


# ---------------------------------------------------------------- vue masquée
def show(s):
    print(f"## {s['id']}")
    for r in s["requirements"]:
        if r["target_type"] == "service":
            who = f"service {r['target_id']} ({r['component_name']})"
        else:
            who = f"flow {r['target_id']} ({r['flow_name']})"
        print(f"  - {who}: {r['kpi_type']} {r['comparator']} {fmt(r['new_value'])} {r['unit']}"
              f"   [valeur de base {fmt(r['baseline_value'])}]")


def pending(scenarios, out):
    """Scénarios sans intention, dans l'ordre du fichier de scénarios."""
    done = {e["scenario_id"] for e in out}
    return [s for s in scenarios if s["id"] not in done]


def cmd_view(args):
    scenarios = load_scenarios()
    todo = pending(scenarios, load_out())
    count = 5
    if args and args[0] != "next":
        ids = [s["id"] for s in scenarios]
        if args[0] not in ids:
            sys.exit(f"id inconnu : {args[0]}")
        todo = scenarios[ids.index(args[0]):]
    if len(args) > 1:
        count = int(args[1])
    for s in todo[:count]:
        show(s)


def cmd_status(_):
    scenarios = load_scenarios()
    out = load_out()
    todo = pending(scenarios, out)
    print(f"enregistrés : {len(out)}/{len(scenarios)}")
    if todo:
        print(f"prochain    : {todo[0]['id']}   (restants : {len(todo)})")
    else:
        print("terminé.")


# ---------------------------------------------------------------- contrôle
def id_present(text, ident):
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(ident) + r"(?![A-Za-z0-9])", text, re.I) is not None


def check_value(kpi, unit, written_value, expected, label, errors):
    factor = UNITS.get(kpi, {}).get(unit.lower())
    if factor is None:
        errors.append(f"{label}: unité '{unit}' non admise pour {kpi}")
        return
    try:
        got = Decimal(written_value) * Decimal(factor)
    except InvalidOperation:
        errors.append(f"{label}: written_value '{written_value}' n'est pas un nombre")
        return
    if got.normalize() != Decimal(repr(expected)).normalize():
        errors.append(f"{label}: {written_value} {unit} = {fmt(float(got))} canonique, attendu {fmt(expected)}")


def check_form(m, text, label, errors):
    form = m.get("written_form", "")
    if not form or form.lower() not in text.lower():
        errors.append(f"{label}: written_form '{form}' absent du texte")
        return
    if m.get("written_unit", "").lower() not in form.lower():
        errors.append(f"{label}: l'unité '{m.get('written_unit')}' n'est pas dans written_form")
    if not m.get("number_in_words"):
        if str(m.get("written_value")) not in re.findall(NUMBER, form):
            errors.append(f"{label}: nombre '{m.get('written_value')}' absent de written_form (number_in_words ?)")
        return
    # valeur en toutes lettres : aucun chiffre, et conversion exacte des mots == written_value
    if re.search(r"\d", form):
        errors.append(f"{label}: number_in_words mais written_form contient des chiffres")
        return
    try:
        _run, val = number_words_in_form(form)
    except ValueError as exc:
        errors.append(f"{label}: nombre en lettres illisible dans '{form}' ({exc})")
        return
    try:
        declared = Decimal(str(m.get("written_value")))
    except InvalidOperation:
        return  # déjà signalé par check_value
    if val.normalize() != declared.normalize():
        errors.append(f"{label}: '{form}' se lit {fmt(float(val))}, written_value déclaré {m.get('written_value')}")


def validate(entry, scen, existing):
    errors, warnings = [], []
    text = entry.get("intent", "")
    if not isinstance(text, str) or not text.strip():
        return ["intent vide"], warnings
    if "\n" in text:
        errors.append("intent sur plusieurs lignes")

    reqs = {(r["target_id"], r["kpi_type"]): r for r in scen["requirements"]}
    mentions = entry.get("mentions", [])
    seen = Counter((m.get("target_id"), m.get("kpi_type")) for m in mentions)
    for key in reqs:
        if seen[key] == 0:
            errors.append(f"exigence {key} absente de mentions")
    for key, c in seen.items():
        if key not in reqs:
            errors.append(f"mention {key} absente du scénario (exigence ajoutée)")
        elif c > 1:
            errors.append(f"mention {key} déclarée {c} fois")

    forms = []
    for m in mentions:
        key = (m.get("target_id"), m.get("kpi_type"))
        r = reqs.get(key)
        if r is None:
            continue
        label = f"{key[0]}/{key[1]}"
        if m.get("comparator") != r["comparator"]:
            errors.append(f"{label}: comparateur {m.get('comparator')} != {r['comparator']}")
        check_value(key[1], m.get("written_unit", ""), m.get("written_value", ""), r["new_value"], label, errors)
        check_form(m, text, label, errors)
        forms.append(m.get("written_form", ""))
        d, lvl = m.get("designation"), m.get("level")
        if d not in DESIGNATIONS:
            errors.append(f"{label}: designation '{d}' inconnue")
            continue
        if lvl not in (1, 2, 3, 4):
            errors.append(f"{label}: level doit être 1..4")
        if lvl == 1 and "id" not in d:
            errors.append(f"{label}: niveau 1 exige une désignation avec id")
        if lvl in (3, 4) and "id" in d:
            errors.append(f"{label}: niveau {lvl} incompatible avec l'id")
        has_id = id_present(text, key[0])
        if "id" in d and not has_id:
            errors.append(f"{label}: désignation '{d}' mais {key[0]} absent du texte")
        if "id" not in d and has_id:
            errors.append(f"{label}: {key[0]} présent dans le texte alors que designation='{d}'")
        if r["target_type"] == "service" and key[1] == "e2e_latency":
            errors.append(f"{label}: latence sur un composant")

    for b in entry.get("baseline_mentions", []):
        key = (b.get("target_id"), b.get("kpi_type"))
        r = reqs.get(key)
        label = f"base {key[0]}/{key[1]}"
        if r is None:
            errors.append(f"{label}: absente du scénario")
            continue
        check_value(key[1], b.get("written_unit", ""), b.get("written_value", ""), r["baseline_value"], label, errors)
        check_form(b, text, label, errors)
        forms.append(b.get("written_form", ""))

    allowed = set()
    for f in forms:
        allowed.update(re.findall(NUMBER, f))
    stripped = re.sub(ID_ANY, " ", text)
    for tok in re.findall(NUMBER, stripped):
        if tok not in allowed:
            errors.append(f"nombre non déclaré dans le texte : {tok}")

    # mots de nombre hors des formes déclarées (règle 9 appliquée aussi aux nombres écrits en lettres)
    rest = text.lower()
    for f in sorted(forms, key=len, reverse=True):
        if f:
            rest = rest.replace(f.lower(), " ")
    for tok in word_tokens(rest):
        if tok in W_FORBIDDEN:
            errors.append(f"mot de nombre non déclaré dans le texte : '{tok}'")
        elif tok in W_SUSPECT:
            warnings.append(f"mot de nombre possible hors valeur déclarée : '{tok}' (vérifier qu'il n'est pas un comptage)")

    for pat, what in FORBIDDEN:
        mt = re.search(pat, text, re.I)
        if mt:
            errors.append(f"terme interdit ({what}) : '{mt.group(0)}'")

    lat_down = any(r["kpi_type"] == "e2e_latency" and r["new_value"] < r["baseline_value"] for r in scen["requirements"])
    lat_up = any(r["kpi_type"] == "e2e_latency" and r["new_value"] > r["baseline_value"] for r in scen["requirements"])
    if re.search(DOWN_WORDS, text, re.I) and not lat_down:
        errors.append("verbe de réduction sans latence resserrée dans ce scénario")
    if re.search(RELAX_WORDS, text, re.I) and not lat_up:
        errors.append("verbe de relâchement sans latence relâchée dans ce scénario")
    if re.search(UP_WORDS, text, re.I) and not any(k in ("cpu", "ram") for _, k in reqs):
        errors.append("verbe d'augmentation sans exigence CPU/RAM dans ce scénario")

    check_directions(entry, scen, text, errors, warnings)
    check_designations(entry, scen, text, errors, warnings)

    plan = entry.get("plan", {})
    for field, vocab in (("style", STYLES), ("register", REGISTERS), ("structure", STRUCTURES)):
        if plan.get(field) not in vocab:
            warnings.append(f"plan.{field}='{plan.get(field)}' hors vocabulaire conseillé")

    n_words = len(text.split())
    if n_words < 5 or n_words > 110:
        warnings.append(f"longueur inhabituelle : {n_words} mots")
    if any(e["intent"].strip().lower() == text.strip().lower() for e in existing):
        errors.append("intent identique à une entrée existante")
    opening = " ".join(text.lower().split()[:3])
    if sum(1 for e in existing if " ".join(e["intent"].lower().split()[:3]) == opening) >= 3:
        warnings.append(f"ouverture '{opening}' déjà utilisée 3 fois ou plus")
    recent = [" ".join(e["intent"].lower().split()[:2]) for e in existing[-4:]]
    if " ".join(text.lower().split()[:2]) in recent:
        warnings.append("même début que l'une des 4 intentions précédentes")
    return errors, warnings


def _sentences(text):
    """Bornes (début, fin) des phrases et propositions séparées par . ; : ! ? ou tiret long."""
    bounds, start = [], 0
    for mt in re.finditer(r"[.;:!?—]\s|[.;:!?]$", text):
        bounds.append((start, mt.end()))
        start = mt.end()
    if start < len(text):
        bounds.append((start, len(text)))
    return bounds


def _expected(req):
    if req["kpi_type"] in ("cpu", "ram"):
        return "up"
    return "down" if req["new_value"] < req["baseline_value"] else "relax"


def check_directions(entry, scen, text, errors, warnings):
    """Chaque marqueur de sens doit être cohérent avec l'exigence à laquelle il se rapporte."""
    reqs = {(r["target_id"], r["kpi_type"]): r for r in scen["requirements"]}
    low = text.lower()
    anchors = []  # (position, exigence)
    for m in entry.get("mentions", []):
        r = reqs.get((m.get("target_id"), m.get("kpi_type")))
        form = (m.get("written_form") or "").lower()
        if r is not None and form and form in low:
            anchors.append((low.index(form), low.index(form) + len(form), r))
    if not anchors:
        return
    sents = _sentences(text)
    for kind, pat in DIR_MARKERS:
        for mt in re.finditer(pat, text, re.I):
            pos = mt.start()
            sent = next(((a, b) for a, b in sents if a <= pos < b), (0, len(text)))
            local = [x for x in anchors if sent[0] <= x[0] < sent[1]] or anchors
            # le marqueur (verbe ou adjectif) précède en général la valeur qu'il qualifie :
            # on prend la première valeur qui le suit dans la phrase, sinon la plus proche avant lui.
            after = [x for x in local if x[1] > pos]
            near = (min(after, key=lambda x: x[0]) if after else max(local, key=lambda x: x[1]))[2]
            exp = _expected(near)
            word = mt.group(0)
            label = f"{near['target_id']}/{near['kpi_type']}"
            if kind != exp:
                if kind == "up" and exp == "relax":
                    warnings.append(f"'{word}' rattaché à {label} (latence relâchée) : préférer un verbe de relâchement")
                else:
                    errors.append(f"marqueur de sens '{word}' incohérent avec l'exigence la plus proche {label} "
                                  f"(attendu : {exp})")
            others = {_expected(x[2]) for x in local if x[2] is not near}
            if kind in ("down", "relax") and ({"down", "relax"} - {kind}) & others:
                warnings.append(f"'{word}' dans une phrase qui porte aussi une exigence de sens opposé : relire")


def check_designations(entry, scen, text, errors, warnings):
    """Filtre lexical des désignations (si designation_text est fourni). Heuristique, non probante."""
    reqs = {(r["target_id"], r["kpi_type"]): r for r in scen["requirements"]}
    for m in entry.get("mentions", []):
        key = (m.get("target_id"), m.get("kpi_type"))
        r = reqs.get(key)
        dt = m.get("designation_text")
        if r is None or dt is None:
            continue
        label = f"{key[0]}/{key[1]}"
        if not dt or dt.lower() not in text.lower():
            errors.append(f"{label}: designation_text '{dt}' absent du texte")
            continue
        d, lvl, tid = m.get("designation", ""), m.get("level"), key[0]
        name = NAMES[tid]
        if "id" in d and not id_present(dt, tid):
            errors.append(f"{label}: designation '{d}' mais {tid} absent de designation_text")
        if "name" in d and name.lower() not in dt.lower():
            errors.append(f"{label}: designation '{d}' mais le nom '{name}' absent de designation_text")
        if lvl in (3, 4) and re.search(re.escape(name), dt, re.I):
            errors.append(f"{label}: niveau {lvl} mais le nom officiel '{name}' figure dans la désignation")
        if lvl == 2 and d == "name" and r["target_type"] == "service" and re.search(r"\b(flow)\b", dt, re.I):
            errors.append(f"{label}: une ressource de composant désignée comme un flux")
        if lvl not in (3, 4):
            continue
        if r["target_type"] == "flow":
            other = "f2" if tid == "f1" else "f1"
            own = re.search(FLOW_CUES[tid], dt, re.I)
            alt = re.search(FLOW_CUES[other], dt, re.I)
            if re.search(GENERIC_FLOW, dt, re.I):
                errors.append(f"{label}: désignation de flux générique proscrite ('{dt}')")
            elif not own and not alt:
                errors.append(f"{label}: désignation de flux sans élément distinctif f1/f2 ('{dt}')")
            elif alt:
                warnings.append(f"{label}: désignation de flux contenant un indice de {other} ('{dt}') : "
                                f"à trancher par la vérification sémantique")
        else:
            own = re.search(CUES[tid], dt, re.I)
            alts = [c for c in CUES if c != tid and re.search(CUES[c], dt, re.I)]
            if not own:
                warnings.append(f"{label}: désignation sans indice lexical de {tid} ('{dt}') : "
                                f"à trancher par la vérification sémantique")
            elif alts:
                warnings.append(f"{label}: désignation contenant aussi des indices de {', '.join(alts)} ('{dt}') : "
                                f"à trancher par la vérification sémantique")


def cmd_append(args):
    if not args:
        sys.exit("usage: append LOT.json")
    with open(args[0], encoding="utf-8") as f:
        batch = json.load(f)
    scenarios = load_scenarios()
    out = load_out()
    saved = 0
    done_ids = {e["scenario_id"] for e in out}
    for entry in batch:
        if entry.get("scenario_id") in done_ids:
            continue  # déjà enregistré lors d'une exécution précédente : on ne réécrit jamais
        todo = pending(scenarios, out)
        if not todo:
            print("tous les scénarios sont déjà enregistrés.")
            break
        scen = todo[0]
        if entry.get("scenario_id") != scen["id"]:
            print(f"STOP : attendu {scen['id']}, reçu {entry.get('scenario_id')}")
            break
        errors, warnings = validate(entry, scen, out)
        for w in warnings:
            print(f"  avertissement {scen['id']}: {w}")
        if errors:
            for e in errors:
                print(f"  ERREUR {scen['id']}: {e}")
            print(f"STOP : {scen['id']} refusé (enregistrés avant : {saved}).")
            break
        stored = {
            "scenario_id": scen["id"],
            "intent": entry["intent"].strip(),
            "review_status": REVIEW_STATUS,
            "variant_id": 1,
            "plan": entry["plan"],
            "mentions": entry["mentions"],
            "baseline_mentions": entry.get("baseline_mentions", []),
            "n_words": len(entry["intent"].split()),
        }
        out.append(stored)
        save_out(out)
        done_ids.add(scen["id"])
        saved += 1
        print(f"ok {scen['id']} ({stored['n_words']} mots)")
    print(f"total enregistré : {len(out)}/{len(scenarios)}")


def cmd_carry(_):
    """Jeu 500 : reprend les intentions déjà écrites et contrôlées du jeu 150, sous leur nouvel id.

    Correspondance par égalité exacte des exigences ; le texte n'est pas modifié ; chaque entrée est
    revalidée contre le scénario du jeu 500.
    """
    if SET != "500":
        sys.exit("carry s'utilise uniquement avec INTENT_SET=500")
    old_scen = json.load(open(ROOT.parent / "eval" / "experiment_150" / "scenarios.json", encoding="utf-8"))
    old_int = {e["scenario_id"]: e for e in json.load(open(ROOT / "intents.json", encoding="utf-8"))}
    new_scen = load_scenarios()
    key = lambda s: json.dumps(s["requirements"], sort_keys=True)
    by_req = {}
    for s in new_scen:
        by_req.setdefault(key(s), []).append(s)
    out = load_out()
    done_ids = {e["scenario_id"] for e in out}
    carried = 0
    for o in old_scen:
        matches = by_req.get(key(o), [])
        if len(matches) != 1:
            sys.exit(f"{o['id']}: {len(matches)} correspondances dans le jeu 500, abandon")
        scen = matches[0]
        if scen["id"] in done_ids:
            continue
        e = old_int[o["id"]]
        errors, _w = validate(e, scen, out)
        if errors:
            sys.exit(f"{o['id']} -> {scen['id']} : revalidation refusée : {errors}")
        stored = dict(e)
        stored["scenario_id"] = scen["id"]
        stored["carried_over_from"] = {"set": "experiment_150", "scenario_id": o["id"]}
        out.append(stored)
        done_ids.add(scen["id"])
        carried += 1
    save_out(out)
    print(f"reportées : {carried} ; total {len(out)}/{len(new_scen)}")


def cmd_revise(args):
    """Corrige à la main une entrée déjà enregistrée : revalidation complète, ancien texte conservé."""
    if len(args) < 2:
        sys.exit("usage: revise LOT.json 'raison'")
    with open(args[0], encoding="utf-8") as f:
        batch = json.load(f)
    scenarios = {s["id"]: s for s in load_scenarios()}
    out = load_out()
    index = {e["scenario_id"]: i for i, e in enumerate(out)}
    for entry in batch:
        sid = entry.get("scenario_id")
        if sid not in index:
            sys.exit(f"{sid}: pas encore enregistré, utiliser append")
        old = out[index[sid]]
        others = [e for e in out if e["scenario_id"] != sid]
        errors, warnings = validate(entry, scenarios[sid], others)
        for w in warnings:
            print(f"  avertissement {sid}: {w}")
        if errors:
            for e in errors:
                print(f"  ERREUR {sid}: {e}")
            sys.exit(f"STOP : révision de {sid} refusée, entrée inchangée.")
        new = dict(old)
        new.update({
            "intent": entry["intent"].strip(),
            "plan": entry["plan"],
            "mentions": entry["mentions"],
            "baseline_mentions": entry.get("baseline_mentions", []),
            "n_words": len(entry["intent"].split()),
            "revision": {"previous_intent": old["intent"], "reason": args[1]},
        })
        out[index[sid]] = new
        save_out(out)
        print(f"révisé {sid} ({new['n_words']} mots)")


def cmd_report(_):
    out = load_out()
    if not out:
        print("rien d'enregistré.")
        return
    mentions = [m for e in out for m in e["mentions"]]
    print(f"entrées : {len(out)}   mentions : {len(mentions)}")
    for title, counter in (
        ("niveaux", Counter(m["level"] for m in mentions)),
        ("désignations", Counter(m["designation"] for m in mentions)),
        ("styles", Counter(e["plan"].get("style") for e in out)),
        ("registres", Counter(e["plan"].get("register") for e in out)),
        ("structures", Counter(e["plan"].get("structure") for e in out)),
        ("unités écrites", Counter(m["written_unit"].lower() for m in mentions)),
    ):
        print(f"{title:15s}", dict(sorted(counter.items(), key=lambda kv: str(kv[0]))))
    cross = Counter((m["kpi_type"], m["level"]) for m in mentions)
    print("kpi x niveau   ", dict(sorted(cross.items())))
    w = [m for m in mentions if m.get("number_in_words")]
    print(f"en lettres     {len(w)}/{len(mentions)} ({100 * len(w) / len(mentions):.1f} %, cible ≈ 10 %)",
          "par kpi", dict(Counter(m["kpi_type"] for m in w)), "par niveau", dict(sorted(Counter(m["level"] for m in w).items())))
    words = [e["n_words"] for e in out]
    print(f"mots : min {min(words)}  médiane {median(words)}  max {max(words)}")
    openings = Counter(" ".join(e["intent"].lower().split()[:2]) for e in out)
    print("ouvertures (2 mots) les plus fréquentes :", openings.most_common(6))


def cmd_blind_export(args):
    """Exporte, pour la vérification sémantique indépendante, le texte et les expressions chiffrées seulement.

    Aucune cible, aucun KPI, aucun niveau, aucune valeur du scénario : le vérificateur doit retrouver
    lui-même, à partir du System Model, à quoi se rapporte chaque expression.
    """
    if not args:
        sys.exit("usage: blind-export SORTIE.json [premier_id] [dernier_id]")
    out = load_out()
    ids = [e["scenario_id"] for e in out]
    lo = ids.index(args[1]) if len(args) > 1 else 0
    hi = ids.index(args[2]) + 1 if len(args) > 2 else len(out)
    rows = []
    for e in out[lo:hi]:
        forms = [m["written_form"] for m in e["mentions"]] + [b["written_form"] for b in e.get("baseline_mentions", [])]
        low = e["intent"].lower()
        forms.sort(key=lambda f: low.index(f.lower()))
        rows.append({"scenario_id": e["scenario_id"], "intent": e["intent"], "value_expressions": forms})
    with open(args[0], "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"{len(rows)} intentions exportées vers {args[0]}")


def cmd_semantic_merge(args):
    """Fusionne un fichier partiel de résolutions à l'aveugle dans semantic_check_<jeu>.json (par scenario_id)."""
    if not args:
        sys.exit("usage: semantic-merge PARTIEL.json [...]")
    cur = {}
    if SEMANTIC.exists():
        with open(SEMANTIC, encoding="utf-8") as f:
            cur = {r["scenario_id"]: r for r in json.load(f)}
    for path in args:
        with open(path, encoding="utf-8") as f:
            for r in json.load(f):
                r["source_file"] = Path(path).name
                cur[r["scenario_id"]] = r
    order = {s["id"]: i for i, s in enumerate(load_scenarios())}
    rows = sorted(cur.values(), key=lambda r: order[r["scenario_id"]])
    with open(SEMANTIC, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"{SEMANTIC.name} : {len(rows)} intentions résolues à l'aveugle")


def semantic_findings(out):
    """Compare les résolutions à l'aveugle aux métadonnées déclarées. Renvoie {scenario_id: [écarts]}."""
    if not SEMANTIC.exists():
        return None
    with open(SEMANTIC, encoding="utf-8") as f:
        res = {r["scenario_id"]: r for r in json.load(f)}
    found = {}
    for e in out:
        r = res.get(e["scenario_id"])
        if r is None or r.get("intent") not in (None, e["intent"]):
            found[e["scenario_id"]] = None  # non vérifié (absent ou texte modifié depuis)
            continue
        by_form = {x["value_expression"].lower(): x for x in r["resolutions"]}
        issues = []
        for m in e["mentions"]:
            x = by_form.get(m["written_form"].lower())
            bound = "min" if m["comparator"] == "gte" else "max"
            if x is None:
                issues.append(f"{m['written_form']}: non résolu")
            elif x.get("target") != m["target_id"]:
                issues.append(f"{m['written_form']}: cible lue {x.get('target')} (candidats {x.get('candidates')}), "
                              f"déclarée {m['target_id']}")
            elif x.get("kpi") != m["kpi_type"] or x.get("bound") != bound:
                issues.append(f"{m['written_form']}: lu {x.get('kpi')}/{x.get('bound')}, déclaré {m['kpi_type']}/{bound}")
        for b in e.get("baseline_mentions", []):
            x = by_form.get(b["written_form"].lower())
            if x is None or x.get("bound") != "previous" or x.get("target") != b["target_id"]:
                issues.append(f"{b['written_form']}: valeur de base mal lue ({x})")
        found[e["scenario_id"]] = issues
    return found


def repetition_stats(out):
    def toks(t):
        return re.findall(r"[a-z0-9']+", t.lower())
    grams = Counter()
    for e in out:
        w = toks(e["intent"])
        grams.update({" ".join(w[i:i + 4]) for i in range(len(w) - 3)})
    sets = [(e["scenario_id"], set(toks(e["intent"]))) for e in out]
    near = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            a, b = sets[i][1], sets[j][1]
            if len(a) >= 8 and len(b) >= 8:
                jac = len(a & b) / len(a | b)
                if jac >= 0.6:
                    near.append([sets[i][0], sets[j][0], round(jac, 3)])
    return {
        "openings_2w": Counter(" ".join(e["intent"].lower().split()[:2]) for e in out).most_common(10),
        "openings_3w_used_3plus": [[k, v] for k, v in Counter(" ".join(e["intent"].lower().split()[:3]) for e in out).items() if v >= 3],
        "top_4grams": [[k, v] for k, v in grams.most_common(15)],
        "near_duplicates_jaccard_ge_0.6": near,
    }


def cmd_audit(_):
    """Revalide chaque entrée enregistrée (contre les entrées qui la précèdent) et écrit le rapport JSON."""
    import datetime
    import hashlib
    scenarios = load_scenarios()
    by_id = {s["id"]: s for s in scenarios}
    out = load_out()
    rows, n_err, n_warn = [], 0, 0
    semantic = semantic_findings(out)
    for i, e in enumerate(out):
        errs, warns = validate(e, by_id[e["scenario_id"]], out[:i])
        sem = semantic.get(e["scenario_id"]) if semantic is not None else None
        sem_status = "not_checked" if sem is None else ("agree" if not sem else "disagree")
        if sem:
            errs = errs + [f"vérification sémantique : {x}" for x in sem]
        n_err += bool(errs)
        n_warn += bool(warns)
        rows.append({"scenario_id": e["scenario_id"], "status": "error" if errs else "ok",
                     "errors": errs, "warnings": warns, "semantic_check": sem_status,
                     "revised": "revision" in e, "n_words": e["n_words"],
                     "n_requirements": len(by_id[e["scenario_id"]]["requirements"])})
    mentions = [m for e in out for m in e["mentions"]]
    words = [m for m in mentions if m.get("number_in_words")]
    order_ok = [e["scenario_id"] for e in out] == [s["id"] for s in scenarios][:len(out)]
    report = {
        "set": SET,
        "scenarios_file": str(SCENARIOS),
        "scenarios_sha256": hashlib.sha256(SCENARIOS.read_bytes()).hexdigest(),
        "intents_file": OUT.name,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "n_scenarios": len(scenarios),
        "n_entries": len(out),
        "complete": len(out) == len(scenarios),
        "order_matches_scenarios": order_ok,
        "entries_with_errors": n_err,
        "entries_with_warnings": n_warn,
        "n_mentions": len(mentions),
        "levels": dict(sorted(Counter(m["level"] for m in mentions).items())),
        "number_in_words": {"count": len(words),
                            "share": round(len(words) / len(mentions), 4) if mentions else 0,
                            "by_kpi": dict(Counter(m["kpi_type"] for m in words)),
                            "by_level": dict(sorted(Counter(m["level"] for m in words).items()))},
        "levels_by_kpi": {k: dict(sorted(Counter(m["level"] for m in mentions if m["kpi_type"] == k).items()))
                          for k in ("cpu", "ram", "e2e_latency")},
        "designations_by_level": {str(l): dict(Counter(m["designation"] for m in mentions if m["level"] == l))
                                  for l in (1, 2, 3, 4)},
        "designation_text_coverage": sum(1 for m in mentions if "designation_text" in m),
        "semantic_check": {
            "file": SEMANTIC.name if SEMANTIC.exists() else None,
            "agree": sum(1 for r in rows if r["semantic_check"] == "agree"),
            "disagree": sum(1 for r in rows if r["semantic_check"] == "disagree"),
            "not_checked": sum(1 for r in rows if r["semantic_check"] == "not_checked"),
        },
        "revisions": [{"scenario_id": e["scenario_id"], **e["revision"]} for e in out if "revision" in e],
        "repetition": repetition_stats(out),
        "entries": rows,
    }
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"{REPORT.name} : {len(out)}/{len(scenarios)} entrées, {n_err} en erreur, {n_warn} avec avertissement, "
          f"{len(words)} valeurs en lettres sur {len(mentions)} mentions")


def main():
    cmds = {"status": cmd_status, "view": cmd_view, "append": cmd_append, "report": cmd_report, "carry": cmd_carry,
            "revise": cmd_revise, "audit": cmd_audit,
            "blind-export": cmd_blind_export, "semantic-merge": cmd_semantic_merge}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
