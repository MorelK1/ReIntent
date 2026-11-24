# Génération d'intentions — campagne V3 (benchmark AR/VR, 500 scénarios)

**Statut : 500/500 intentions enregistrées** dans `intents_v3.json`, `review_status = llm_generated_auto_checked`.
Aucune validation humaine indépendante n'a eu lieu. Audit : `validation_report_v3.json`.

## 1. Méthode

Rédaction manuscrite, scénario par scénario, selon `SKILL.md`, `PROMPT_GENERATION_INTENTS.md` et
`CONSIGNES_GENERATION_INTENTS_FR.md`. Aucun gabarit, aucune phrase à trous, aucun tirage aléatoire de
formulations. Le seul code est `intent_store.py`, qui affiche les exigences masquées, contrôle et enregistre.

- Les scénarios n'ont été lus qu'avec `intent_store.py view`, qui affiche uniquement l'id, la cible, le KPI, le
  comparateur, la valeur de base, la nouvelle valeur et l'unité. `category`, `level`, `decision`,
  `feasible_placements`, `minimum_migrations`, `moved_components`, `p1`, `p1_latency_ms`, `flow_budgets_ms`,
  `origin`, `factor` et `budget_factor` n'ont jamais été affichés pendant la rédaction.
- Ni les scénarios techniques ni le placement initial P0 n'ont été modifiés.
- Ordre strict de `scenarios.json`. Lots de 17 à 23 scénarios par fichier, au lieu des 5 à 10 indiqués dans
  SKILL §4, pour limiter le nombre d'appels. L'enregistrement reste fait scénario par scénario, avec arrêt au
  premier refus, et le fichier final ne porte aucune trace du découpage.
- Avant chaque lot, j'ai consulté `report` et pris volontairement les styles, registres, structures, ouvertures et
  niveaux sous-représentés.
- Générateur : Claude (Anthropic). Extracteur évalué dans le pipeline : `openai/gpt-5.4-mini` via OpenRouter
  (configurable). Les vérificateurs sémantiques à l'aveugle (§5) sont aussi des instances de Claude : ils sont
  indépendants par leur contexte, pas par leur modèle.

## 2. Compatibilité du protocole avec V3

| Point | Constat | Effet |
|---|---|---|
| Volume | 500 scénarios, 1053 exigences (1 exig. : 122 ; 2 : 268 ; 3 : 45 ; 4 : 65) | compatible |
| Cibles | CPU/RAM sur `C2`, `C3`, `C4` (jamais `C1`, `C5`) ; latence sur `f1` (231) et `f2` (196) | compatible |
| Valeurs | CPU 3.5–16 vCPU, RAM 2.5–13 GB (pas de 0.5) ; latence entière 292–530 ms ; base 500 ms | conversions et lettres exactes |
| Direction | CPU/RAM toujours en hausse ; latence resserrée 340 fois, relâchée 87 fois ; 36 scénarios mixtes | contrôle par exigence (§3) |
| Nouveaux champs | `origin`, `component_id`, `resource`, `factor`, `budget_factor`, `path` | jamais affichés |

**Unités binaires.** CONSIGNES §3.1 autorise GiB/MiB « avec convention explicite », alors que PROMPT règle 2
les interdit. J'ai appliqué la règle la plus stricte (PROMPT) : aucune unité binaire.

## 3. Modifications du validateur (`intent_store.py`)

Toutes les règles de SKILL.md et PROMPT_GENERATION_INTENTS.md sont conservées. Ajouts, dans l'ordre :

1. **Organisation V3.** `INTENT_SET=v3` écrit dans `intents_v3.json`, `validation_report_v3.json` et
   `semantic_check_v3.json`. `INTENT_SCENARIOS` fixe le chemin des scénarios. Les jeux 150 et 500 sont
   inchangés et leurs fichiers ne sont pas touchés.
2. **Valeurs en toutes lettres.** La forme écrite ne contient aucun chiffre. Les mots sont convertis en décimal
   exact, puis comparés à `written_value` (lui-même × facteur d'unité = valeur du scénario). Grammaire admise :
   entiers jusqu'à 999 999, « point » suivi de chiffres, fractions finales « and a half / a quarter / three
   quarters ». Les formes mal construites sont refusées.
3. **Règle 9 pour les mots.** Un nombre en lettres non déclaré (« two teams ») est une erreur. « one », « half »,
   « couple »… ne donnent qu'un avertissement.
4. **Sens par exigence.** Chaque marqueur de sens (verbes de SKILL plus « faster/slower/tighter/looser/leeway/
   extra time ») est rattaché à la première valeur qui le suit dans la même phrase, ou à défaut à la plus proche
   qui le précède. Il doit concorder avec cette exigence : hausse CPU/RAM, latence resserrée ou relâchée.
   L'ancien contrôle par scénario est conservé. Un avertissement signale une phrase qui porte deux latences de
   sens opposés.
5. **Filtre des désignations** (champ optionnel `designation_text`, renseigné pour 1047 mentions sur 1053 ; seules
   les pilotes 001, 003 et 005 n'en ont pas) :
   - `id` et `name` doivent figurer dans `designation_text` ;
   - en L3/L4, le nom officiel de la cible est interdit ;
   - une désignation de flux générique (« main pipeline », « video path ») ou sans élément distinctif f1/f2 est
     refusée ;
   - les indices lexicaux absents ou mélangés produisent un avertissement « à trancher par la vérification
     sémantique ».

   **Ce filtre est lexical : il ne prouve jamais l'univocité d'une désignation.** Il trie les cas à faire juger
   par la vérification indépendante.
6. **Vérification sémantique indépendante.** `blind-export` exporte le texte et les expressions chiffrées, sans
   cible ni KPI. `semantic-merge` rassemble les résolutions faites à l'aveugle. `audit` compare cible, KPI et
   borne (min/max/valeur antérieure) aux métadonnées, et compte tout écart ou résolution manquante comme une
   erreur. Une résolution devient « non vérifiée » dès que le texte change.
7. **`audit`.** Produit le rapport JSON : empreinte SHA-256 des scénarios, ordre, erreurs et avertissements par
   entrée, distributions, révisions, statistiques de répétition et quasi-doublons (Jaccard ≥ 0.6).

**Bug corrigé pendant la génération.** La première version du point 4 rattachait le marqueur à la valeur la plus
proche en caractères. Elle a refusé à tort ARVR-000241 : dans « …up to 516 ms, but squeeze the path… to 476 ms »,
« squeeze » visait bien 476. J'ai corrigé la règle (rattachement à la valeur suivante), ajouté un test de
non-régression, et revalidé les 240 entrées déjà enregistrées sans aucun nouveau refus.

**Tests de non-régression** (`test_intent_store_v3.py`, tous passants) :
- parseur de nombres : 15 formes acceptées, 13 refusées ;
- cas « mots faux, valeur déclarée juste » et chiffres dans une forme en lettres ;
- nombre en lettres non déclaré ;
- sens par exigence dans un scénario mixte, y compris l'inversion que l'ancien contrôle acceptait et le cas 241 ;
- verbe de hausse sur une latence ;
- désignations : nom officiel en L3, flux générique, flux sans indice, renvoi à la vérification sémantique,
  `designation_text` absent du texte.

## 4. Résultats

### Niveaux d'explicitation (cible 20/30/30/20)

| | L1 | L2 | L3 | L4 | total |
|---|---|---|---|---|---|
| toutes exigences | 210 (19.9 %) | 315 (29.9 %) | 317 (30.1 %) | 211 (20.0 %) | 1053 |
| CPU | 19.4 % | 32.0 % | 29.5 % | 19.1 % | 319 |
| RAM | 17.9 % | 30.0 % | 30.3 % | 21.8 % | 307 |
| latence | 21.8 % | 28.3 % | 30.4 % | 19.4 % | 427 |

Par taille de scénario (1, 2, 3, 4 exigences), chaque niveau reste entre 16 et 32 %. Désignations : name 315,
context 211, id 207, role 187, flow 130, id+name 3.

### Valeurs en toutes lettres (cible ≈ 10 %)

**105 sur 1053 (10.0 %).** Par KPI : CPU 45, latence 34, RAM 26. Par niveau : L1 24, L2 24, L3 36, L4 21.
Formes utilisées : entiers (« three hundred and eighty-five milliseconds »), fractions (« eleven and a half
vCPUs »), « point » (« four point five virtual cores ») et milliers (« seven thousand millicores »).

### Diversité

- **Styles :** contrainte 120, ticket 75, besoin 73, récit 61, impératif 53, demande polie 45, objectif 42,
  question 31.
- **Registres :** développeur 112, ops 97, familier 86, formel 83, manager 70, product owner 52.
- **Structures :** une phrase 268, énumération 92, propositions enchaînées 70, deux phrases 55, conditionnelle 15.
- **Unités :**
  - CPU : vCPU(s) 256, millicores 36, mCPU 13, virtual CPUs 11, virtual cores 3 ;
  - RAM : GB 245, MB 39, gigabytes 21, megabytes 2 ;
  - latence : ms 387, milliseconds 25, seconds 13, µs 2.
- **Longueur :** 5 à 52 mots, médiane 20, moyenne 21.8.
- **Répétition :**
  - aucune intention identique ;
  - aucune ouverture de 2 mots utilisée plus de 7 fois sur 500 ;
  - 4-grammes les plus fréquents : les noms officiels « the Object Recognition Flow » (63) et « the AR Rendering
    Flow » (49), puis des tournures de comparateur (« 5 vCPUs or more »…) ;
  - 3 paires de quasi-doublons (Jaccard 0.60–0.62 : 088/408, 215/272, 413/447), avec des valeurs et des structures
    différentes, conservées.

### Sens de la latence

Les 36 scénarios mixtes sont rédigés soit sans verbe de direction, soit avec un verbe attaché à la bonne exigence,
ce que vérifie le contrôle par exigence. Une seule mention de valeur de base (ARVR-000080, « from 500 ms »),
déclarée dans `baseline_mentions`.

## 5. Vérification sémantique indépendante

Pour chaque lot, un agent sans accès aux fichiers de travail a lu à l'aveugle le texte et les expressions
chiffrées, avec le seul System Model. Pour chaque expression, il a donné la cible (ou « ambiguous » / « unclear »),
les candidats, le KPI et la borne. Les écarts avec les métadonnées sont des erreurs d'audit. À partir du lot
086–180, la consigne demandait aussi de signaler les chemins de latence partiels, et pour le dernier passage les
bornes inférées d'une formulation faible.

**Bilan final : 500/500 intentions concordantes, 0 désaccord, 0 non vérifiée.** Les 1054 expressions (1053
exigences et 1 valeur de base) ont reçu une seule cible, toujours la cible déclarée. Aucune n'est marquée
« ambiguous » ou « unclear ». Les bornes sont toutes justes : 626 min, 427 max, 1 valeur antérieure.

| Passage | Portée | Signalements ayant conduit à une révision |
|---|---|---|
| 1 | 001–025 | aucun (25/25 concordants) |
| 2 | 026–085 | 057 (C1/C3/C5 en lecture stricte), 042 et 065 (chemin partiel) |
| 3 | 086–180 | 104, 129, 138, 156, 165, 173, 175, 178 (chemin partiel ou rang d'étape) |
| 4 | 181–300 | 191 et 200 (point de départ non dit) |
| 5 | 301–400 | 375 (borne non dite), d'où la recherche globale des comparateurs implicites (§6) |
| 6–7 | 401–500 et les 26 entrées révisées après leur premier passage | aucun |

**Signalements non bloquants du dernier passage.** 29 « BOUND » portent sur des latences dont le plafond est
donné par le verbe (« tighten to », « relax to », « trim to »), comme le prévoit le protocole (« comparateur
porté par la tournure »). Toutes ont été lues comme des maxima. Un lecteur plus strict pourrait demander un
plafond explicite pour ces cas.

## 6. Corrections (29 entrées révisées avec `revise`, ancien texte conservé dans `revision`)

| Entrées | Problème | Correction |
|---|---|---|
| 002 | L4 « whatever works out what is actually in each captured frame » lisible comme C2 ou C3 (relecture humaine) | « the stage the recognition chain goes through but the rendering chain skips » |
| 004 | « the overlay painter is gasping », peu naturel (relecture humaine) | « the last stop before the display on the AR side keeps running out of compute » |
| 057 | « the processing step common to both chains » : C1/C3/C5 en lecture stricte | « the stage both chains share between capture and display » |
| 013, 025, 042, 065, 104, 129, 138, 139, 156, 165, 175, 178 | latence décrite sur une partie du chemin (début à la détection, fin avant l'affichage, point de départ absent) | reformulée de bout en bout, capture → affichage |
| 085, 138, 173 | rang d'étape (« third », « second », « first processing stage ») dépendant du comptage de la capture | position relative non ambiguë (« last stage before the display », « right after capture ») |
| 062, 145, 182, 264, 309, 323, 328, 358, 375, 431 | comparateur implicite ou ambigu (« targets », « ask for 8.5 vCPUs », « to hit », valeur sans borne) | comparateur explicite (« ceilings », « at least », « at most », « within ») |
| 191, 200 | point de départ de la latence non dit | « within … of capture » |

**Refus du validateur pendant la rédaction, corrigés dans le texte avant enregistrement :**
- ARVR-000121 : nom officiel « AR Rendering » dans une désignation L3 ;
- ARVR-000438 : « How about », qui contient le mot interdit « about ».

## 7. Limites

- **Pas de revue humaine.** `review_status` reste `llm_generated_auto_checked`. La vérification sémantique est faite
  par un modèle de la même famille que le générateur : elle réduit le risque d'ambiguïté sans l'éliminer, et un
  lecteur humain ou l'extracteur évalué peut trancher autrement.
- **Le filtre lexical n'est pas une preuve.** 102 entrées gardent au moins un avertissement :
  - désignations positionnelles sans indice lexical de la cible, tranchées par la vérification sémantique ;
  - phrases qui portent deux latences de sens opposés, relues ;
  - quelques ouvertures répétées.
- **Désignations L4 positionnelles.** Beaucoup reposent sur la topologie (« the stage only the AR chain uses »,
  « the stage the detector hands off to ») : elles évaluent le raisonnement sur le System Model plutôt que la
  paraphrase. Les noms officiels restent fréquents en L2, conformément au protocole.
- **Le validateur ne mesure ni le naturel ni la pertinence du contexte ajouté** (« holiday peak », « trade-show
  build »…). Le contexte ajouté ne contient jamais de nombre ni d'exigence supplémentaire.
- **Données synthétiques.** Ne pas présenter ces intentions comme des demandes réelles d'opérateurs.
- Les entrées 001, 003 et 005 (pilotes non corrigés) n'ont pas de `designation_text` : leur désignation n'est
  couverte que par la vérification sémantique.
- Hors périmètre : intentions sans seuil chiffré, variantes multiples par scénario, accord inter-annotateurs.

## 8. Fichiers

| Fichier | Contenu |
|---|---|
| `intents_v3.json` | 500 intentions (texte, plan, mentions, métadonnées de révision) |
| `validation_report_v3.json` | audit complet des 500 entrées (contrôles mécaniques et vérification sémantique) |
| `semantic_check_v3.json` | résolutions à l'aveugle (cible, KPI, borne, justification) pour chaque expression |
| `intent_store.py` | script adapté (rétrocompatible 150/500) |
| `test_intent_store_v3.py` | tests de non-régression du validateur |
| `intent_generation_report_v3.md` | ce rapport |
