"""Structured output schema for the single-LLM-call "direct" baseline.

See prompt.py's own docstring for why this (and the sibling cot/schema.py)
differ from expérimentations/public_safety/baseline/schema.py: same shape
(reuses ExtractedRequirement so the two are field-for-field comparable),
different domain underneath (eval/'s workload_mi/mips/output_size_mb/
fixed_placement system model, not wcet_ms).

Field order matters less here than in cot/schema.py: runner.py doesn't use
with_structured_output (see its own docstring for why -- plain chat
completion + manual JSON parsing, for OpenRouter model coverage), so this
class is used for POST-HOC validation of the parsed JSON, not to constrain
generation order. The actual field-order instruction the model follows
lives in prompt.py's own text. Still declared in the same order as that
text, for readability.
"""

from typing import Literal

from pydantic import BaseModel, Field


class BaselineResult(BaseModel):
    decision: Literal["STAY", "OFFLOAD", "INFEASIBLE"] = Field(
        description="STAY if the current placement already satisfies every new requirement and every "
        "other service/flow's own baseline requirement; OFFLOAD if a different full placement does; "
        "INFEASIBLE if no placement satisfies everything simultaneously"
    )
    placement: dict[str, str] = Field(
        description="The full placement you propose: every service_id mapped to the node_id it should "
        "run on. For STAY, this must be identical to the current placement. For INFEASIBLE, return the "
        "current placement unchanged. Every fixed_placement=True service must keep its current_node "
        "here, in every outcome."
    )
    explanation: str = Field(description="Plain-language explanation of the decision and why")
