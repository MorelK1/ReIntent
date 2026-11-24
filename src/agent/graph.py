"""Assembly of the StateGraph matching the architecture diagram.

User Input -> Intent Grounding -> Necessity Checker --Yes--> Decision Engine -\\
                                                    \\--No----------------------> Trace & Explanation Generation -> User Input
"""

from langgraph.graph import END, StateGraph

from agent.nodes.decision_engine_cp_sat import decision_engine_node_cp_sat
from agent.nodes.explanation import explanation_node
from agent.nodes.intent_grounding import intent_grounding_node
from agent.nodes.necessity_checker import necessity_checker_node
from agent.states.state import AgentState


def _route_after_necessity_check(state: AgentState) -> str:
    if state["necessity_result"].status == "reconfiguration_required":
        return "decision_engine"
    return "explanation"


def build_graph(
    model: str | None = None,
    prompt_strategy: str = "zero_shot",
    explanation_model: str | None = None,
    include_explanation: bool = True,
    skip_necessity_checker: bool = False,
):
    """model overrides OPENROUTER_MODEL (.env) for intent_grounding_node when
    given -- None keeps today's behavior exactly. prompt_strategy picks
    intent_grounding_node's prompting strategy (see PROMPT_STRATEGIES there).
    explanation_model independently overrides explanation_node's model --
    defaults to model itself (both nodes share one override by default), so
    passing only model still affects both, but the two can be set to
    different models when a comparison specifically calls for it (e.g.
    cheaper/faster model for prose explanation while varying the extraction
    model under test).

    include_explanation=False builds a graph that stops right after
    necessity_checker/decision_engine, skipping explanation_node entirely --
    for evaluations that only care about extraction/decision correctness
    (explanation_node is a pure epilogue, it never feeds back into the
    decision, and it roughly doubles the LLM cost per run). Still a real
    compiled StateGraph, just a shorter one -- not a manually-chained
    substitute. Default True keeps every existing caller's behavior
    unchanged.

    skip_necessity_checker=True builds a graph WITHOUT the necessity_checker
    node at all -- intent_grounding connects directly to decision_engine,
    which then runs on every scenario regardless of whether the current
    placement would already have satisfied the requirements. This is the
    "how much does necessity_checker actually matter" ablation: a true
    removal, not a bypassed/ignored routing decision -- necessity_checker
    is simply never added to the graph, so state["necessity_result"] stays
    at whatever the caller's initial state set it to (typically None).
    explanation.py's build_user_prompt handles necessity_result=None (and,
    independently, decision_result.decision == {}) with the same neutral
    wording it uses for the normal "nothing needed to move" case, so
    explanation_node's own latency/content doesn't become a second,
    unintended variable on top of decision_engine always running. Default
    False keeps every existing caller's behavior unchanged.

    All of this exists so experimentation scripts can build a graph with a
    different model/strategy/shape without touching this file or the node
    implementations."""
    if explanation_model is None:
        explanation_model = model

    graph = StateGraph(AgentState)

    graph.add_node(
        "intent_grounding",
        lambda state: intent_grounding_node(state, model=model, prompt_strategy=prompt_strategy),
    )
    graph.add_node("decision_engine", decision_engine_node_cp_sat)
    graph.set_entry_point("intent_grounding")

    if skip_necessity_checker:
        graph.add_edge("intent_grounding", "decision_engine")
    else:
        graph.add_node("necessity_checker", necessity_checker_node)
        graph.add_edge("intent_grounding", "necessity_checker")

    if include_explanation:
        graph.add_node("explanation", lambda state: explanation_node(state, model=explanation_model))
        if not skip_necessity_checker:
            graph.add_conditional_edges(
                "necessity_checker",
                _route_after_necessity_check,
                {"decision_engine": "decision_engine", "explanation": "explanation"},
            )
        graph.add_edge("decision_engine", "explanation")
        graph.add_edge("explanation", END)
    else:
        if not skip_necessity_checker:
            # Same routing labels from _route_after_necessity_check, just
            # mapped straight to END instead of to an "explanation" node.
            graph.add_conditional_edges(
                "necessity_checker",
                _route_after_necessity_check,
                {"decision_engine": "decision_engine", "explanation": END},
            )
        graph.add_edge("decision_engine", END)

    return graph.compile()
