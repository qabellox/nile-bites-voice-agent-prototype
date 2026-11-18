"""Structured tool gateway — A1 component ``gateway``.

The gateway sits between the model and the **same** :class:`ToolRegistry` the
baseline uses. It is a proxy, not a replacement:

* ``llm_schemas()`` is delegated untouched, so the model sees a byte-identical
  tool list in both arms. That is a methodological requirement, not a nicety —
  a different tool list would make the architecture comparison meaningless.
* the registered tools keep doing all business resolution (item aliases, prices,
  zones). The gateway never duplicates that logic, because a second source of
  truth for "what is this item" would eventually disagree with the first.

What it adds is **structure**: schema-checked arguments before dispatch, a
precise error the model can act on, duplicate-mutation suppression, and per-call
timing so the component's real cost is measured rather than assumed.

What it must never do is add knowledge. It cannot invent a quantity, resolve an
ambiguous item, or fill a default. A gateway that guessed would be a second model
in the loop, which the design forbids.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

#: Tools that change business state. Used for idempotency bookkeeping and by the
#: orchestrator, which must never reorder these.
MUTATING_TOOLS = frozenset(
    {"create_order", "modify_order", "cancel_order", "confirm_order"}
)

#: Only ``create_order`` is deduplicated. It is the one tool where an identical
#: repeat inside a single turn is essentially always the model repeating itself.
#: ``modify_order`` is deliberately excluded: "add one more" twice is a legitimate
#: pair of calls with identical arguments, and suppressing the second would lose a
#: real order line.
IDEMPOTENT_TOOLS = frozenset({"create_order"})


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _as_integer(value: Any) -> int | None:
    """Accept an int, or a string that is an integer literal. Nothing else.

    ``"2"`` -> ``2`` is a transport artefact worth absorbing. ``"two"`` or ``2.7``
    is a real disagreement about the quantity, and absorbing it would silently
    change an order.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text and (text.isdigit() or (text[0] in "+-" and text[1:].isdigit())):
            try:
                return int(text)
            except ValueError:  # pragma: no cover - defensive
                return None
    return None


def validate_arguments(
    schema: dict[str, Any], arguments: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Validate and minimally normalize arguments against a tool's schema.

    Returns ``(normalized, errors)``. ``errors`` is empty on success. Validation
    is deliberately shallow — it checks what the schema declares and nothing
    more, so it cannot become a second, disagreeing implementation of the tool.
    """
    errors: list[str] = []
    normalized = _normalize(arguments, schema, "arguments", errors)
    return normalized, errors


def _normalize(
    value: Any, schema: Any, path: str, errors: list[str]
) -> Any:
    if not isinstance(schema, dict):
        return value

    expected = schema.get("type")

    if expected == "object":
        if not isinstance(value, dict):
            errors.append(f"{path}: expected object, got {_type_name(value)}")
            return value
        properties = schema.get("properties") or {}
        result: dict[str, Any] = {}
        for required in schema.get("required") or []:
            if required not in value:
                errors.append(f"{path}.{required}: required argument is missing")
        for key, item in value.items():
            if key in properties:
                result[key] = _normalize(item, properties[key], f"{path}.{key}", errors)
            elif schema.get("additionalProperties", True) is False:
                errors.append(f"{path}.{key}: argument is not in the tool schema")
            else:
                result[key] = item
        return result

    if expected == "array":
        if not isinstance(value, (list, tuple)):
            errors.append(f"{path}: expected array, got {_type_name(value)}")
            return value
        items_schema = schema.get("items")
        out = [
            _normalize(item, items_schema, f"{path}[{index}]", errors)
            for index, item in enumerate(value)
        ]
        minimum_items = schema.get("minItems")
        if isinstance(minimum_items, int) and len(out) < minimum_items:
            errors.append(
                f"{path}: needs at least {minimum_items} item(s), got {len(out)}"
            )
        return out

    if expected == "integer":
        coerced = _as_integer(value)
        if coerced is None:
            errors.append(f"{path}: expected integer, got {_type_name(value)}")
            return value
        value = coerced
    elif expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{path}: expected number, got {_type_name(value)}")
            return value
    elif expected == "string":
        if not isinstance(value, str):
            errors.append(f"{path}: expected string, got {_type_name(value)}")
            return value
    elif expected == "boolean":
        if not isinstance(value, bool):
            errors.append(f"{path}: expected boolean, got {_type_name(value)}")
            return value

    allowed = schema.get("enum")
    if allowed is not None and value not in allowed:
        errors.append(f"{path}: expected one of {list(allowed)}, got {value!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            errors.append(f"{path}: {value} is below the minimum {minimum}")
        if maximum is not None and value > maximum:
            errors.append(f"{path}: {value} is above the maximum {maximum}")

    return value


@dataclass
class GatewayDecision:
    """What the gateway did with one call, in arrival order."""

    arrival: int
    name: str
    accepted: bool
    error_code: str | None
    reasons: list[str] = field(default_factory=list)
    normalized_arguments: dict[str, Any] = field(default_factory=dict)
    original_arguments: dict[str, Any] = field(default_factory=dict)
    validation_ms: float = 0.0
    dispatch_ms: float = 0.0
    reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "arrival": self.arrival,
            "name": self.name,
            "accepted": self.accepted,
            "error_code": self.error_code,
            "reasons": list(self.reasons),
            "normalized_arguments": self.normalized_arguments,
            "original_arguments": self.original_arguments,
            "validation_ms": round(self.validation_ms, 4),
            "dispatch_ms": round(self.dispatch_ms, 4),
            "reused": self.reused,
        }


class ToolGateway:
    """Schema-validating proxy around a :class:`ToolRegistry`."""

    def __init__(
        self,
        registry: Any,
        *,
        enabled: bool = True,
        validate: bool = True,
        idempotency: bool = True,
    ) -> None:
        self.registry = registry
        self.enabled = bool(enabled)
        self.validate = bool(validate)
        self.idempotency = bool(idempotency)
        self._decisions: list[GatewayDecision] = []
        self._completed: dict[str, dict[str, Any]] = {}
        self._arrival = 0

    # -- surface the tool loop needs ------------------------------------
    @property
    def components(self) -> dict[str, bool]:
        return {"gateway": self.enabled}

    def llm_schemas(self) -> list[dict[str, Any]]:
        return self.registry.llm_schemas()

    def tool_names(self) -> list[str]:
        return self.registry.tool_names()

    def take_calls(self) -> list[dict[str, Any]]:
        return self.registry.take_calls()

    def take_decisions(self) -> list[dict[str, Any]]:
        out = [decision.to_dict() for decision in self._decisions]
        self._decisions = []
        return out

    @property
    def total_validation_ms(self) -> float:
        return sum(decision.validation_ms for decision in self._decisions)

    # -- dispatch --------------------------------------------------------
    def dispatch(self, name: str, arguments: dict[str, Any] | None) -> Any:
        if not self.enabled:
            # Ablation off: exactly the baseline path, no extra bookkeeping. A
            # disabled component that still changed behaviour would not be an
            # ablation.
            return self.registry.dispatch(name, arguments)

        arrival = self._arrival
        self._arrival += 1
        original = dict(arguments or {})
        started = time.perf_counter()

        schema = self._parameters_schema(name)
        if schema is None:
            # Unknown tool: let the registry produce its canonical UNKNOWN_TOOL
            # response. Re-implementing that error here would create two
            # different messages for the same mistake.
            result = self.registry.dispatch(name, original)
            self._record(
                arrival, name, False, "UNKNOWN_TOOL", ["tool is not registered"],
                original, original, time.perf_counter() - started, 0.0, False,
            )
            return result

        normalized = original
        reasons: list[str] = []
        if self.validate:
            normalized, errors = validate_arguments(schema, original)
            if errors:
                validation_ms = (time.perf_counter() - started) * 1000.0
                result = {
                    "ok": False,
                    "error": "VALIDATION_FAILED",
                    "tool": name,
                    "detail": "; ".join(errors),
                    "received": original,
                }
                self._record(
                    arrival, name, False, "VALIDATION_FAILED", errors,
                    original, normalized, validation_ms, 0.0, False,
                )
                return result

        key = self._idempotency_key(name, normalized)
        if key is not None and key in self._completed:
            validation_ms = (time.perf_counter() - started) * 1000.0
            reused = dict(self._completed[key])
            reused["reused"] = True
            self._record(
                arrival, name, True, None, ["duplicate mutation suppressed"],
                original, normalized, validation_ms, 0.0, True,
            )
            return reused

        dispatched_at = time.perf_counter()
        validation_ms = (dispatched_at - started) * 1000.0
        result = self.registry.dispatch(name, normalized)
        dispatch_ms = (time.perf_counter() - dispatched_at) * 1000.0

        succeeded = not (isinstance(result, dict) and result.get("ok") is False)
        if key is not None and succeeded and isinstance(result, dict):
            self._completed[key] = result

        self._record(
            arrival, name, succeeded, None if succeeded else str(
                (result or {}).get("error") if isinstance(result, dict) else "TOOL_ERROR"
            ),
            reasons, original, normalized, validation_ms, dispatch_ms, False,
        )
        return result

    # -- internals -------------------------------------------------------
    def _parameters_schema(self, name: str) -> dict[str, Any] | None:
        node = self.registry.schemas.get(name) if hasattr(self.registry, "schemas") else None
        if not isinstance(node, dict):
            return None
        function = node.get("function")
        if not isinstance(function, dict):
            return None
        parameters = function.get("parameters")
        return parameters if isinstance(parameters, dict) else None

    def _idempotency_key(
        self, name: str, arguments: dict[str, Any]
    ) -> str | None:
        if not self.idempotency or name not in IDEMPOTENT_TOOLS:
            return None
        try:
            payload = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return None
        return f"{name}:{payload}"

    def _record(
        self,
        arrival: int,
        name: str,
        accepted: bool,
        error_code: str | None,
        reasons: list[str],
        original: dict[str, Any],
        normalized: dict[str, Any],
        validation_ms: float,
        dispatch_ms: float,
        reused: bool,
    ) -> None:
        self._decisions.append(
            GatewayDecision(
                arrival=arrival,
                name=name,
                accepted=accepted,
                error_code=error_code,
                reasons=list(reasons),
                normalized_arguments=normalized,
                original_arguments=original,
                validation_ms=validation_ms,
                dispatch_ms=dispatch_ms,
                reused=reused,
            )
        )
