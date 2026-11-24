# ReIntent

ReIntent: Intent-Driven Reconfiguration for Evolving QoS Requirements in the Cloud-Edge Continuum

Given a natural-language operator intent (e.g. "object recognition now needs
4.5 GB of memory"), the pipeline extracts its structured requirements,
checks whether the current placement of services across nodes still
satisfies them, and — only if not — searches for a new placement that does,
using an exact CP-SAT solver (provably optimal, or a minimal set of
conflicting constraints if no placement exists). A final step turns the
decision into a short natural-language explanation.

The pipeline is built as a **LangGraph** graph with four stages: intent
grounding (requirement extraction) → necessity checker → decision engine →
explanation.

## Setup

```bash
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in OPENROUTER_API_KEY at minimum
```
