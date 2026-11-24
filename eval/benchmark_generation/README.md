# Benchmark AR/VR — Placement initial hétérogène, C2 sur Edge

## Configuration de référence

Application 1, composants et versions conservés depuis le placement GA :

| Composant | Version | Nœud | Tier |
|---|---|---|---|
| C1 Vision Capture | V1 | N12 | User/IoT |
| C2 Object Recognition | V3 | N4 | Edge |
| C3 Scene Processing | V2 | N6 | Cloud |
| C4 AR Rendering | V5 | N4 | Edge |
| C5 Display Output | V1 | N8 | Helper/IoT |

Les applications 2–4 conservent leurs versions et placements GA, et constituent la charge de fond. Capacités CPU (MIPS), vCPU, RAM et stockage vérifiées pour toutes les applications ensemble.

**SLO initiaux:** f1 = 500 ms, f2 = 500 ms. Latences initiales recalculées : f1 = 473,101999 ms, f2 = 481,686668 ms.

## Fichiers

- `initial_placement.json` : les 20 affectations, incluant l'application cible et les applications de fond.
- `system_model/nodes.csv` : 15 nœuds; vCPU, RAM/stockage en GB décimaux, CPU en MIPS.
- `system_model/links.csv` : liens bidirectionnels, bande passante Mbps, délai unidirectionnel RTT/2 (ms).
- `system_model/services.json` : C1–C5, versions sélectionnées, exigences, MI, tailles en Mb, placement.
- `system_model/flows.json` : graphe des deux chemins applicatifs, budgets et latences de référence.
- `system_model/background_loads.csv` : charge cumulative de App2–App4 par nœud.
- `componentsConnections.json` : dépendances applicatives complètes.
- `benchmark_model.py` : calcul des chemins les plus rapides, ressources, classifications et vérification du System Model.
- `build_benchmark.py` : génération synthétique stratifiée du benchmark.
- `output/experiment_500/` : 500 scénarios et statistiques de test.

## Reproduire la génération

Lancer depuis le dossier du projet :

```bash
python3 build_benchmark.py --num-scenarios 500 --offload-ratio 0.60 --stay-ratio 0.30 --infeasible-ratio 0.10 --seed 42 --output-dir output/experiment_500
```

Résultat vérifié : STAY 150, OFFLOAD 300, INFEASIBLE 50. Le mélange est fixé *après classification*, non issu de fréquences observées en production.

## Conventions et limites

- La RAM et le stockage d'origine sont en MB ; exports System Model en GB = MB/1000 ; le solveur travaille en MB.
- Les tailles de données sont en **Mb**, bande passante en **Mbps**. RTT source en secondes ; la latence de propagation d'un sens est RTT/2.
- Délai communication par plus court chemin pondéré par `dataSize_Mb / bandwidth_Mbps + RTT_seconds / 2` pour chaque arc. Traitement = `cpuWorkloadMI / cpuMIPS_du_noeud`.
- Les différences d'exigences de CPU/RAM modifient la demande réservée, **pas** la charge de traitement MI, les versions ni la topologie.
- Le placement initial est **construit sous une contrainte de diversité Edge–Cloud**, pas une sortie optimale directe du GA.
- Le modèle ne simule pas la congestion et les files d'attente ; les résultats ne démontrent pas les performances réelles d'un déploiement.
- Les choix de budgets et les intervalles de génération sont des adaptations du contexte ECSP-Sim pour ce benchmark.
