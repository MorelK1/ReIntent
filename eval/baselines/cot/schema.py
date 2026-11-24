"""Structured output schema for the single-LLM-call "CoT" baseline.

Matches prompt.py's _RULES Step 6 JSON template -- a deliberately different
shape from the sibling direct-llm/schema.py's BaselineResult: instead of one
big freeform `reasoning` scratchpad field, the intermediate reasoning steps
(Step 1's requirement identification, Step 2/3's feasibility check, Step 4's
migration count) are each captured as their OWN structured field. Field
order still matters for the same reason as before -- requirements and
verification come before decision/placement in the JSON text, so the model
still can't have silently committed to an answer before working through
them -- just distributed across named fields instead of one text blob.

Field order matters less here than in with_structured_output-based nodes:
runner.py calls plain chat completion (see its own docstring), so this
class validates the parsed JSON after the fact, not during generation. The
actual field-order instruction the model follows lives in prompt.py's own
text; declared in the same order here for readability.
"""

from typing import Literal

from pydantic import BaseModel, Field


class RequirementItem(BaseModel):
    target: str = Field(description="service_id (for cpu/ram) or flow_id (for latency) this threshold targets")
    metric: Literal["cpu", "ram", "latency"] = Field(description="Which KPI this threshold applies to")
    operator: Literal["gte", "lte", "eq"] = Field(description="Comparison operator applied to value")
    value: float = Field(description="Numeric threshold, in whatever unit the intent itself stated it")
    unit: str = Field(description="Unit exactly as stated in the intent -- never converted here; "
                       "convert internally when checking feasibility, report the unit as written")


class ViolatedConstraint(BaseModel):
    target: str = Field(description="service_id or flow_id whose constraint is violated -- not "
                         "necessarily one of `requirements` above: a knock-on capacity/latency "
                         "violation on an unrelated service/flow counts too")
    metric: Literal["cpu", "ram", "latency"] = Field(description="Which KPI is violated")
    reason: str = Field(description="Short, factual explanation of the violation -- e.g. 'requires "
                         ">=5 vCPU, only 4 vCPU available on edge_B'")


class Verification(BaseModel):
    current_placement_feasible: bool = Field(
        description="Whether the CURRENT placement already satisfies every requirement above and "
        "every other service/flow's own unchanged baseline"
    )
    violated_requirements: list[ViolatedConstraint] = Field(
        description="Empty if current_placement_feasible is true. Otherwise, one entry per violated "
        "requirement or constraint."
    )


class CoTBaselineResult(BaseModel):
    requirements: list[RequirementItem] = Field(
        description="Every requirement explicitly stated in the intent -- one entry per distinct "
        "target+metric threshold. A compound intent (e.g. both a CPU and a RAM threshold for the same "
        "service) produces more than one entry here, not one entry averaging/combining them."
    )
    verification: Verification
    decision: Literal["STAY", "OFFLOAD", "INFEASIBLE"] = Field(
        description="STAY if the current placement already satisfies every new requirement and every "
        "other service/flow's own baseline requirement; OFFLOAD if a different full placement does; "
        "INFEASIBLE if no placement satisfies everything simultaneously"
    )
    placement: dict[str, str] | None = Field(
        description="The full placement you propose: every service_id mapped to the node_id it should "
        "run on. For STAY, identical to the current placement. For INFEASIBLE, null -- nothing is "
        "applied since nothing would fix it. Every fixed_placement=True service must keep its "
        "current_node here, in every decision."
    )
    migration_count: int = Field(
        description="Number of services whose assigned node in `placement` differs from their "
        "current_node. 0 for STAY and INFEASIBLE."
    )
    justification: str = Field(description="Brief, factual explanation of the decision and why")
