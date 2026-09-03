"""The agent surface as tools a model can call.

An agent decides what to write. Where that decision is an algorithm, the
application calls the methods of ``AgentBoard`` itself and needs nothing here.
Where the decision is a language model, the application cannot hand those
methods to a model API, because such an API takes a schema for each thing it
may call and answers with a request to call one by name. This module renders
the same methods into that form and runs what comes back.

The rendering is not a copy of the protocol, and the five differences are why
it belongs here rather than in each application.

The agent's name is absent from every schema. ``AgentBoard`` carries it, so a
model cannot write under a name other than the one the caller bound.

``idempotency_key`` is absent from every schema and is filled from the
identifier the model API gives the call. A loop that sends a call twice
therefore writes once.

An outcome the model can act on comes back as text it can read, because a model
reads results and catches no exceptions. A rejected write and a premise whose
version moved are values on the protocol already. A region the board does not
hold is raised there and answered here, naming the regions the board does hold.

A read that answers with a list is bounded. A list too large for a model to
hold is cut, and the result says how many entries it left out. A premise's
value is answered whole, because one value cut in half is a value the model
cannot use.

Acknowledgment is not among the tools. Acknowledging says the agent has
stopped working on a notification, and the caller running the loop is what
knows that, so it stays a call the caller makes.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, overload

from blackboard._board import (
    BlackboardError,
    BoardChange,
    Conflict,
    Contribution,
    IdempotencyKeyError,
    Level,
    Premise,
    PremiseState,
    RegionKindError,
    UndeclaredRegionError,
    UnsetPremiseError,
    Written,
)
from blackboard._control import (
    AgentBoard,
    PremiseError,
    Rejected,
    RunClosedError,
    UnknownNotificationError,
)

__all__ = [
    "MAX_RESULT_BYTES",
    "TOOLS",
    "TOOL_PREFIX",
    "ToolAnnotations",
    "ToolDescriptor",
    "ToolResult",
    "ToolSet",
    "UnknownToolError",
    "definitions",
    "for_anthropic",
    "for_openai",
    "run",
]

#: Prefixed to every tool name. The board's tools are made to sit in a toolset
#: the board does not own, so the prefix keeps them from colliding with the
#: caller's own tools and marks which tools reach the board.
TOOL_PREFIX = "blackboard_"

#: The byte budget for a result that answers with a list. A list that would
#: exceed it is cut, and the result says how many entries it left out. One entry
#: is always kept, so a single entry larger than this comes back whole.
MAX_RESULT_BYTES = 16384


class UnknownToolError(BlackboardError):
    """A name that no descriptor in this toolset carries."""


#: What a call raises that the model itself can correct or must be told about.
#: Everything else, a store that cannot be reached above all, is the caller's
#: to handle and is left to travel.
_ANSWERABLE: tuple[type[BlackboardError], ...] = (
    UndeclaredRegionError,
    RegionKindError,
    UnsetPremiseError,
    IdempotencyKeyError,
    UnknownNotificationError,
    RunClosedError,
    PremiseError,
)


@dataclass(frozen=True)
class ToolAnnotations:
    """What a tool does to the board, for a caller choosing which to offer.

    ``read_only`` is false for a tool that writes. ``idempotent`` says that
    running the same call twice leaves the board as running it once did, which
    holds when the caller passes the model call's identifier, because a write
    takes that identifier as its idempotency key.
    """

    read_only: bool
    idempotent: bool = True


@dataclass(frozen=True)
class ToolDescriptor:
    """One method of ``AgentBoard``, as a model API accepts it.

    ``method`` names the method this calls, and is what ties the schema to the
    protocol: the parameters the schema offers, together with those
    ``withholds`` names, are exactly that method's parameters, and a test holds
    them to it.

    ``withholds`` names the parameters deliberately absent from the schema.
    ``from_call_id`` names those the caller's identifier for the model call
    fills in, and every one of them is withheld.
    """

    name: str
    description: str
    method: str
    input_schema: dict[str, Any]
    annotations: ToolAnnotations
    withholds: tuple[str, ...] = ()
    from_call_id: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolResult:
    """What one tool call produced.

    ``content`` is what goes back to the model. ``is_error`` marks a call the
    model got wrong, which it can correct and send again; a write the run
    refused is not one of those, because the model asked correctly and the run
    said no. ``value`` is what the board returned, for a caller that wants the
    outcome rather than its rendering, and is ``None`` where nothing was
    returned or the call did not reach the board.
    """

    content: str
    is_error: bool = False
    value: object = None


def _schema(properties: dict[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
    }


_LEVEL = {"type": "string", "description": "The name of the level."}
_PREMISE = {"type": "string", "description": "The name of the premise."}
_FROM_SEQUENCE = {
    "type": "integer",
    "description": "Where to start reading. 0 reads from the beginning.",
}

_DESCRIPTORS: tuple[ToolDescriptor, ...] = (
    ToolDescriptor(
        name=f"{TOOL_PREFIX}read_regions",
        description=(
            "Lists the regions this board holds and the kind of each one. A level "
            "accumulates contributions in the order they were written. A premise "
            "holds one current value under a version. Call this when you do not "
            "know what the board holds."
        ),
        method="read_regions",
        input_schema=_schema({}),
        annotations=ToolAnnotations(read_only=True),
    ),
    ToolDescriptor(
        name=f"{TOOL_PREFIX}read_level",
        description=(
            "Reads contributions from one level, in the order they were written, "
            "starting at a sequence number. Answers with the contributions and the "
            "sequence to continue from, so a later call reads only what arrived "
            "after this one."
        ),
        method="read_level",
        input_schema=_schema(
            {"level": _LEVEL, "from_sequence": _FROM_SEQUENCE}, required=["level"]
        ),
        annotations=ToolAnnotations(read_only=True),
        withholds=("limit",),
    ),
    ToolDescriptor(
        name=f"{TOOL_PREFIX}read_premise",
        description=(
            "Reads the current value of one premise and the version that value "
            f"carries. Changing a premise requires that version, so read it here "
            f"before calling {TOOL_PREFIX}set_premise."
        ),
        method="read_premise",
        input_schema=_schema({"premise": _PREMISE}, required=["premise"]),
        annotations=ToolAnnotations(read_only=True),
    ),
    ToolDescriptor(
        name=f"{TOOL_PREFIX}read_board",
        description=(
            "Reads every write to every region of this board in one order, starting "
            "at a sequence number. Answers with the writes and the sequence to "
            "continue from. Use this to catch up on the whole board rather than on "
            "one region."
        ),
        method="read_board",
        input_schema=_schema({"from_sequence": _FROM_SEQUENCE}),
        annotations=ToolAnnotations(read_only=True),
        withholds=("limit",),
    ),
    ToolDescriptor(
        name=f"{TOOL_PREFIX}write",
        description=(
            "Adds a contribution to a level, where every other agent on this board "
            "can read it. A level accumulates, so this adds to the level and "
            "replaces nothing already there. The content is stored as JSON. Call "
            f"{TOOL_PREFIX}read_regions first if you do not know which levels this "
            "board holds."
        ),
        method="write",
        input_schema=_schema(
            {
                "level": _LEVEL,
                "content": {"description": "The contribution. Any JSON value."},
            },
            required=["level", "content"],
        ),
        annotations=ToolAnnotations(read_only=False),
        withholds=("idempotency_key",),
        from_call_id=("idempotency_key",),
    ),
    ToolDescriptor(
        name=f"{TOOL_PREFIX}set_premise",
        description=(
            "Replaces the value of a premise. Read the premise first and pass the "
            "version you read as expected_version. Where another agent has written "
            "to that premise since the read, nothing is changed and the answer "
            "carries the version now current, so read again and decide from the "
            "value that is now there."
        ),
        method="set_premise",
        input_schema=_schema(
            {
                "premise": _PREMISE,
                "value": {"description": "The new value. Any JSON value."},
                "expected_version": {
                    "type": "integer",
                    "description": (
                        f"The version {TOOL_PREFIX}read_premise last answered with."
                    ),
                },
            },
            required=["premise", "value", "expected_version"],
        ),
        annotations=ToolAnnotations(read_only=False),
        withholds=("idempotency_key",),
        from_call_id=("idempotency_key",),
    ),
)


class ToolSet(Sequence[ToolDescriptor]):
    """Descriptors under unique names, rendered together and dispatched to.

    A caller offers a set to a model and runs what the model asks for against
    a board. Selecting a subset answers with another set, so a caller that
    offers a model reads alone dispatches through the same object it offered.
    """

    def __init__(self, descriptors: Iterable[ToolDescriptor]) -> None:
        self._descriptors = tuple(descriptors)
        self._by_name = {d.name: d for d in self._descriptors}
        if len(self._by_name) != len(self._descriptors):
            raise ValueError("two descriptors carry one name")

    def __len__(self) -> int:
        return len(self._descriptors)

    def __iter__(self) -> Iterator[ToolDescriptor]:
        return iter(self._descriptors)

    @overload
    def __getitem__(self, index: int) -> ToolDescriptor: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[ToolDescriptor]: ...

    def __getitem__(
        self, index: int | slice
    ) -> ToolDescriptor | Sequence[ToolDescriptor]:
        return self._descriptors[index]

    def __repr__(self) -> str:
        return f"ToolSet({', '.join(d.name for d in self._descriptors)})"

    def owns(self, name: str) -> bool:
        """Says which tool calls belong to this set.

        A model answers with the name of a tool, and the caller's own tools
        sit in the same offer, so this is what a caller asks before handing a
        call to ``run``.
        """
        return name in self._by_name

    def descriptor(self, name: str) -> ToolDescriptor:
        """Returns the descriptor under this name, or raises."""
        found = self._by_name.get(name)
        if found is None:
            held = ", ".join(sorted(self._by_name)) or "no tools"
            raise UnknownToolError(f"{name!r} is not one of {held}")
        return found

    def select(self, *names: str) -> ToolSet:
        """Returns the descriptors under these names, in this set's order."""
        chosen = {self.descriptor(name).name for name in names}
        return ToolSet(d for d in self._descriptors if d.name in chosen)

    def read_only(self) -> ToolSet:
        """Returns the tools that read the board and never write to it."""
        return ToolSet(d for d in self._descriptors if d.annotations.read_only)

    def definitions(self) -> list[dict[str, Any]]:
        """Returns each tool in the shape the Model Context Protocol defines.

        A server answering ``tools/list`` returns this as it stands. The two
        provider shapes carry the same name, description and schema under the
        key names each of those APIs uses.
        """
        return [
            {
                "name": d.name,
                "description": d.description,
                "inputSchema": d.input_schema,
                "annotations": {
                    "readOnlyHint": d.annotations.read_only,
                    "idempotentHint": d.annotations.idempotent,
                },
            }
            for d in self._descriptors
        ]

    def for_anthropic(self) -> list[dict[str, Any]]:
        """Returns the tools as the Anthropic Messages API takes them."""
        return [
            {
                "name": d.name,
                "description": d.description,
                "input_schema": d.input_schema,
            }
            for d in self._descriptors
        ]

    def for_openai(self) -> list[dict[str, Any]]:
        """Returns the tools as the OpenAI chat completions API takes them."""
        return [
            {
                "type": "function",
                "function": {
                    "name": d.name,
                    "description": d.description,
                    "parameters": d.input_schema,
                },
            }
            for d in self._descriptors
        ]

    def run(
        self,
        board: AgentBoard,
        name: str,
        arguments: Mapping[str, Any],
        *,
        call_id: str | None = None,
    ) -> ToolResult:
        """Runs one call the model asked for, against this board.

        ``name`` and ``arguments`` are what the model API answered with, and
        ``call_id`` is that API's identifier for the call, which becomes the
        write's idempotency key. A name this set does not hold raises, because
        the caller's own tools are the caller's to route.

        Arguments are checked before the board is touched, so a malformed call
        changes nothing and comes back saying what was wrong with it.
        """
        descriptor = self.descriptor(name)
        complaint = _complain(descriptor, arguments)
        if complaint is not None:
            return ToolResult(content=complaint, is_error=True)
        taken = {
            key: value
            for key, value in arguments.items()
            if key in descriptor.input_schema["properties"]
        }
        if call_id is not None:
            for parameter in descriptor.from_call_id:
                taken[parameter] = call_id
        try:
            value: object = getattr(board, descriptor.method)(**taken)
        except _ANSWERABLE as answered:
            return ToolResult(content=_explain(board, answered), is_error=True)
        return ToolResult(content=_render(descriptor, taken, value), value=value)


#: Every tool this module offers, in the order the documentation lists them.
TOOLS = ToolSet(_DESCRIPTORS)


def definitions() -> list[dict[str, Any]]:
    """Returns every tool in the shape the Model Context Protocol defines."""
    return TOOLS.definitions()


def for_anthropic() -> list[dict[str, Any]]:
    """Returns every tool as the Anthropic Messages API takes them."""
    return TOOLS.for_anthropic()


def for_openai() -> list[dict[str, Any]]:
    """Returns every tool as the OpenAI chat completions API takes them."""
    return TOOLS.for_openai()


def run(
    board: AgentBoard,
    name: str,
    arguments: Mapping[str, Any],
    *,
    call_id: str | None = None,
) -> ToolResult:
    """Runs one call the model asked for, against this board."""
    return TOOLS.run(board, name, arguments, call_id=call_id)


# Checking what the model sent


_TYPE_NAMES = {"string": "a string", "integer": "an integer"}


def _is_of_type(value: object, named: str) -> bool:
    if named == "string":
        return isinstance(value, str)
    if named == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    return True


def _complain(descriptor: ToolDescriptor, arguments: Mapping[str, Any]) -> str | None:
    """Returns what is wrong with these arguments, or nothing.

    An argument the schema does not name is ignored rather than refused, since
    it changes nothing about the call that is made.
    """
    properties: dict[str, Any] = descriptor.input_schema["properties"]
    for needed in descriptor.input_schema["required"]:
        if needed not in arguments:
            return (
                f"{descriptor.name} needs {needed!r}, which this call left out. "
                f"It takes {', '.join(sorted(properties))}."
            )
    for given, value in arguments.items():
        expected = properties.get(given, {}).get("type")
        if expected is not None and not _is_of_type(value, expected):
            return (
                f"{given!r} takes {_TYPE_NAMES[expected]}, and this call sent "
                f"{type(value).__name__}."
            )
    return None


# Turning what the board answered into what the model reads
#
# Every store refuses content that JSON cannot carry, and the conformance suite
# holds each one to that, so what a read answers with is already JSON and
# nothing here needs a fallback for a value JSON has no form for.


def _fit(build: Callable[[int], dict[str, Any]], total: int) -> str:
    """Serialises the largest prefix of a result that fits the byte budget.

    One entry is kept whatever its size, so the budget bounds how many entries
    come back rather than the bytes exactly. A read that answered with nothing
    would carry the sequence bound it was given, and a caller following that
    bound would ask for the same page for ever.
    """
    floor = 1 if total else 0
    kept = total
    while True:
        text = json.dumps(build(kept))
        if len(text.encode()) <= MAX_RESULT_BYTES or kept <= floor:
            return text
        kept = kept - 1 if kept <= 4 else kept * 3 // 4


def _sequenced(
    items: Sequence[Contribution] | Sequence[BoardChange],
    key: str,
    entry: Callable[[Any], dict[str, Any]],
    from_sequence: int,
) -> str:
    """Renders a read of ordered writes, with the sequence to continue from."""

    def build(kept: int) -> dict[str, Any]:
        held = items[:kept]
        body: dict[str, Any] = {key: [entry(item) for item in held]}
        body["next_from_sequence"] = held[-1].sequence + 1 if held else from_sequence
        if kept < len(items):
            body["omitted"] = len(items) - kept
        return body

    return _fit(build, len(items))


def _render(descriptor: ToolDescriptor, taken: Mapping[str, Any], value: object) -> str:
    """Renders what the board answered, in the form the model reads."""
    if isinstance(value, Rejected):
        return json.dumps(
            {"rejected": {"cause": value.cause.value, "reason": value.reason}}
        )
    if isinstance(value, Conflict):
        return json.dumps(
            {
                "conflict": {
                    "current_version": value.current_version,
                    "reason": (
                        "the premise moved since the version this call named. "
                        "Read it again and decide from the value now there."
                    ),
                }
            }
        )
    if isinstance(value, Written):
        written: dict[str, Any] = {"sequence": value.sequence}
        if value.version is not None:
            written["version"] = value.version
        if value.repeated:
            written["repeated"] = True
        return json.dumps(written)
    if isinstance(value, PremiseState):
        return json.dumps({"value": value.value, "version": value.version})
    if descriptor.method == "read_regions":
        regions = list(value) if isinstance(value, list) else []
        return _fit(
            lambda kept: _regions_body(regions, kept),
            len(regions),
        )
    if descriptor.method == "read_level":
        return _sequenced(
            list(value) if isinstance(value, list) else [],
            "contributions",
            lambda c: {"sequence": c.sequence, "content": c.content},
            int(taken.get("from_sequence", 0)),
        )
    if descriptor.method == "read_board":
        return _sequenced(
            list(value) if isinstance(value, list) else [],
            "changes",
            lambda c: {
                "sequence": c.sequence,
                "region": c.region,
                "content": c.content,
            },
            int(taken.get("from_sequence", 0)),
        )
    return json.dumps(value)


def _regions_body(regions: list[Any], kept: int) -> dict[str, Any]:
    body: dict[str, Any] = {
        "regions": [
            {"name": r.name, "kind": "level" if isinstance(r, Level) else "premise"}
            for r in regions[:kept]
        ]
    }
    if kept < len(regions):
        body["omitted"] = len(regions) - kept
    return body


def _explain(board: AgentBoard, answered: BlackboardError) -> str:
    """Renders a raised condition as what the model needs to correct it."""
    said = str(answered) or type(answered).__name__
    if isinstance(answered, (UndeclaredRegionError, RegionKindError)):
        held = _held(board)
        if held:
            return f"{said}. This board holds {held}."
    return said


def _held(board: AgentBoard) -> str:
    """Names the regions the board holds, for a call that named one it does not."""
    try:
        regions = board.read_regions()
    except BlackboardError:  # pragma: no cover - a board that cannot be read
        return ""
    levels = [r.name for r in regions if isinstance(r, Level)]
    premises = [r.name for r in regions if isinstance(r, Premise)]
    parts = []
    if levels:
        parts.append("the levels " + ", ".join(repr(n) for n in levels))
    if premises:
        parts.append("the premises " + ", ".join(repr(n) for n in premises))
    return " and ".join(parts)
