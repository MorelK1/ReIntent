"""Calls a single-LLM baseline directly (no LangGraph -- one call does
everything) -- shared by both "direct llm"/ and cot/, since the LLM-calling
mechanics (build the prompt, invoke, parse + validate the JSON response,
measure time/tokens) don't depend on which prompt module or schema class is
used, only on the ones passed in by the caller (see run_sample.py).

Plain chat completion + manual JSON parsing, NOT with_structured_output --
same reason as expérimentations/public_safety/baseline/runner.py (which
this is modeled on): some OpenRouter-routed models/providers (e.g.
meta-llama/llama-4-scout via deepinfra/novita) have NO endpoint supporting
tool-calling or response_format at all, so plain invoke is the only
universally-working path across every candidate model, at the cost of
parsing the response ourselves -- including stripping a markdown code
fence some models wrap their JSON in regardless of being asked not to.

Consequence worth being explicit about: unlike the real pipeline's nodes
(intent_grounding_node, explanation_node), which use with_structured_output
and so get field order enforced by the provider's own constrained decoding,
here the field order a model follows (e.g. cot/'s `reasoning` before
`outcome`) is enforced ONLY by prompt.py's own textual instruction plus the
model's instruction-following -- not by any API-level guarantee. Still a
real, meaningful distinction between direct-llm/ and cot/'s mechanics (the
model still has to actually write `reasoning` tokens before `outcome`
tokens in the JSON text it emits), just a softer one than schema-level
constrained decoding would give.
"""

import json
import re
import time

from pydantic import BaseModel, ValidationError

from agent.model.schemas import Flow, Link, Node, Service
from agent.utils.llm_openrouter import get_openrouter_gpt_llm

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

_COMPARATOR_SYNONYMS = {
    "<=": "lte", "=<": "lte", "≤": "lte", "<": "lte",
    ">=": "gte", "=>": "gte", "≥": "gte", ">": "gte",
    "=": "eq", "==": "eq",
}


def _normalize(data: dict) -> dict:
    """Same tolerant pre-validation cleanup as public_safety's baseline --
    see its runner.py for the full rationale (no provider-side schema
    enforcement keeps the model on exact literals/types once structured
    output isn't used): a comparator given as a symbol instead of the
    schema's word, or a new_placement value as a bare JSON number instead
    of a string."""
    # "comparator": no baseline schema uses that key anymore (cot/'s
    # RequirementItem renamed it "operator" when _RULES was redesigned;
    # direct-llm/'s schema dropped the whole requirements list). Check both
    # keys so this still fires regardless of which baseline is running.
    for req in data.get("requirements") or []:
        for key in ("comparator", "operator"):
            value = req.get(key)
            if isinstance(value, str) and value.strip() in _COMPARATOR_SYNONYMS:
                req[key] = _COMPARATOR_SYNONYMS[value.strip()]
    # new_placement: cot/'s schema. placement: direct llm/'s (no requirements
    # field there anymore) -- both can suffer the same bare-number-instead-
    # of-string issue, so both get the same coercion.
    for key in ("new_placement", "placement"):
        if isinstance(data.get(key), dict):
            data[key] = {k: str(v) for k, v in data[key].items()}
    return data


def _extract_json_object(text: str) -> str | None:
    """Finds the first balanced {...} object in text, scanning past any
    narrative prose before and after it -- some models (observed: llama-3.3-
    70b-instruct, every strategy; gemini-2.5-flash-lite in verbose zero_shot)
    wrap a otherwise-valid JSON answer in explanatory text despite being told
    not to, which the plain fence-strip below can't recover from (it only
    strips a fence marker at a LINE's own start/end, not the prose around
    it). Brace-depth counting tracks string literals, so a brace inside a
    quoted string value doesn't throw off the count."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        start = text.find("{", start + 1)
    return None


def _parse_response(content: str, schema_cls: type[BaseModel]) -> tuple[BaseModel | None, str | None]:
    stripped = _FENCE_RE.sub("", content.strip())
    try:
        # strict=False tolerates a literal control character (e.g. an
        # unescaped newline) inside a JSON string value -- a common LLM
        # formatting slip, observed from gemini-2.5-flash-lite, that strict
        # JSON parsing rejects outright even though the intent is clear.
        data = json.loads(stripped, strict=False)
    except json.JSONDecodeError as first_exc:
        extracted = _extract_json_object(content)
        if extracted is None:
            return None, f"Invalid JSON: {first_exc}"
        try:
            data = json.loads(extracted, strict=False)
        except json.JSONDecodeError as exc:
            return None, f"Invalid JSON: {exc}"
    try:
        return schema_cls.model_validate(_normalize(data)), None
    except ValidationError as exc:
        return None, str(exc)


def run_baseline(
    intent_text: str,
    services: dict[str, Service],
    flows: dict[str, Flow],
    nodes: dict[str, Node],
    links: list[Link],
    prompt_module,
    schema_cls: type[BaseModel],
    model: str | None = None,
    prompt_strategy: str | None = None,
) -> dict:
    """prompt_module: the already-imported "direct llm"/prompt.py or cot/
    prompt.py module (see run_sample.py:_load_baseline_module) -- must
    expose PROMPT_STRATEGIES, DEFAULT_PROMPT_STRATEGY, build_user_prompt.
    schema_cls: the matching Pydantic model (BaselineResult or
    CoTBaselineResult) to validate the response against.

    Returns {"result": schema_cls | None, "raw_response": str, "model": str,
    "elapsed_s": float, "token_usage": dict | None, "parsing_error": str |
    None} -- same shape convention as intent_grounding_node's telemetry and
    public_safety's own baseline runner, so scoring code can treat all of
    them the same way. `raw_response` is the model's response text
    EXACTLY as received, before the markdown-fence strip, JSON parse, or
    _normalize()'s tolerant repairs -- so a repair (e.g. ">=" -> "gte")
    never silently erases what the model actually wrote; `result` stays
    the validated, repaired object for everything that just wants to use
    the data.
    """
    prompt_strategy = prompt_strategy or prompt_module.DEFAULT_PROMPT_STRATEGY
    system_prompt = prompt_module.PROMPT_STRATEGIES[prompt_strategy]
    # Observed: gpt-oss-20b/120b, called via plain invoke (no
    # with_structured_output here, see this module's own docstring),
    # sometimes spend their ENTIRE max_tokens budget on hidden reasoning
    # tokens and leave response.content empty -- 8189/8192 output tokens
    # were "reasoning" in one capture. with_structured_output-based nodes
    # (intent_grounding_node, explanation_node) never showed this, so the
    # cap is lowered here only, not in llm_openrouter.py's shared builder.
    # Models without a reasoning concept (gemini, mistral, llama) silently
    # ignore an inapplicable OpenRouter field.
    llm = get_openrouter_gpt_llm(model).bind(extra_body={"reasoning": {"effort": "low"}})
    resolved_model = llm.bound.model_name
    user_prompt = prompt_module.build_user_prompt(intent_text, services, flows, nodes, links)

    start = time.perf_counter()
    response = llm.invoke([("system", system_prompt), ("human", user_prompt)])
    elapsed_s = time.perf_counter() - start

    result, parsing_error = _parse_response(response.content, schema_cls)
    token_usage = response.usage_metadata
    return {
        "result": result,
        "raw_response": response.content,
        "model": resolved_model,
        "elapsed_s": elapsed_s,
        "token_usage": dict(token_usage) if token_usage else None,
        "parsing_error": parsing_error,
    }
