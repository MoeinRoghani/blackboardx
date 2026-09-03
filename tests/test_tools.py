"""The agent surface projected into what a model API accepts.

`blackboard.tools` renders the reads and writes of `AgentBoard` as tool
schemas, and runs what a model asks for against a board. The projection
withholds the agent's name and the idempotency key from every schema, and
turns each outcome a model can act on into text it can read, so these tests
hold the projection to the protocol rather than to a copy of it.
"""

from __future__ import annotations

import inspect
import json
from datetime import timedelta
from typing import Any

import pytest

from blackboard import (
    Agent,
    AgentBoard,
    Control,
    InMemoryStore,
    Level,
    Premise,
    Reject,
    RunLimits,
    create_model,
    tools,
)

BOARD = "incident-1"
LIMITS = RunLimits(wall_clock=timedelta(minutes=5), idle=timedelta(minutes=5))


def a_run(**overrides: Any) -> Control:
    settings: dict[str, Any] = {
        "board_id": BOARD,
        "store": InMemoryStore(),
        "regions": [Level("signals"), Level("findings"), Premise("severity")],
        "premises": {"severity": "unknown"},
        "agents": [Agent(name="triage", notify=lambda n: None)],
        "limits": LIMITS,
    }
    settings.update(overrides)
    return create_model(**settings).control


def a_board(**overrides: Any) -> AgentBoard:
    return a_run(**overrides).as_agent("triage")


# What the toolset holds


def test_the_toolset_covers_the_reads_and_writes_of_the_protocol() -> None:
    assert [d.method for d in tools.TOOLS] == [
        "read_regions",
        "read_level",
        "read_premise",
        "read_board",
        "write",
        "set_premise",
    ]


def test_acknowledgment_is_not_a_tool() -> None:
    """Acknowledging says the agent has stopped, which only the caller knows."""
    assert "ack" not in [d.method for d in tools.TOOLS]
    assert not [d for d in tools.TOOLS if d.name.endswith("ack")]


def test_every_name_carries_the_prefix() -> None:
    """The board's tools sit in a toolset it does not own, so they are marked."""
    for descriptor in tools.TOOLS:
        assert descriptor.name.startswith(tools.TOOL_PREFIX)
        assert descriptor.name == f"{tools.TOOL_PREFIX}{descriptor.method}"


def test_a_name_is_checked_against_the_toolset() -> None:
    assert tools.TOOLS.owns("blackboard_write")
    assert not tools.TOOLS.owns("my_own_tool")


# The projection holds to the protocol


def test_a_schema_names_the_parameters_of_the_method_it_calls() -> None:
    """The check that keeps a hand-written schema from drifting from the code."""
    for descriptor in tools.TOOLS:
        method = getattr(AgentBoard, descriptor.method)
        parameters = set(inspect.signature(method).parameters) - {"self"}
        offered = set(descriptor.input_schema["properties"])
        assert offered | set(descriptor.withholds) == parameters, descriptor.name
        assert not offered & set(descriptor.withholds), descriptor.name


def test_no_schema_asks_the_model_for_an_identity() -> None:
    """`AgentBoard` carries the name, so a model cannot write as another agent."""
    for descriptor in tools.TOOLS:
        assert "writer" not in descriptor.input_schema["properties"]
        assert "agent" not in descriptor.input_schema["properties"]


def test_no_schema_asks_the_model_for_an_idempotency_key() -> None:
    for descriptor in tools.TOOLS:
        assert "idempotency_key" not in descriptor.input_schema["properties"]
    writes = [d for d in tools.TOOLS if not d.annotations.read_only]
    assert writes
    for descriptor in writes:
        assert "idempotency_key" in descriptor.from_call_id


def test_what_is_taken_from_the_call_id_is_also_withheld() -> None:
    for descriptor in tools.TOOLS:
        assert set(descriptor.from_call_id) <= set(descriptor.withholds)


def test_every_description_says_what_comes_back() -> None:
    for descriptor in tools.TOOLS:
        assert len(descriptor.description) > 80, descriptor.name


# Rendering into a provider's shape


def test_the_anthropic_shape_carries_the_schema_under_input_schema() -> None:
    rendered = tools.for_anthropic()
    assert {t["name"] for t in rendered} == {d.name for d in tools.TOOLS}
    first = rendered[0]
    assert set(first) == {"name", "description", "input_schema"}
    assert first["input_schema"]["type"] == "object"


def test_the_openai_shape_nests_the_schema_under_function() -> None:
    rendered = tools.for_openai()
    assert {t["function"]["name"] for t in rendered} == {d.name for d in tools.TOOLS}
    first = rendered[0]
    assert first["type"] == "function"
    assert set(first["function"]) == {"name", "description", "parameters"}


def test_both_shapes_carry_the_same_schemas() -> None:
    for anthropic, openai in zip(
        tools.for_anthropic(), tools.for_openai(), strict=True
    ):
        assert anthropic["input_schema"] == openai["function"]["parameters"]
        assert anthropic["description"] == openai["function"]["description"]


def test_a_rendered_toolset_joins_a_caller_s_own_tools() -> None:
    """The call site this exists for: our tools beside theirs, in one list."""
    mine = [{"name": "search", "description": "...", "input_schema": {}}]
    offered = tools.for_anthropic() + mine
    assert len(offered) == len(tools.TOOLS) + 1
    assert offered[-1] == mine[0]


def test_a_subset_renders_on_its_own() -> None:
    only_reads = tools.TOOLS.read_only()
    assert [d.method for d in only_reads] == [
        "read_regions",
        "read_level",
        "read_premise",
        "read_board",
    ]
    assert len(only_reads.for_anthropic()) == 4


def test_a_named_subset_renders_on_its_own() -> None:
    chosen = tools.TOOLS.select("blackboard_write", "blackboard_read_level")
    assert [d.method for d in chosen] == ["read_level", "write"]


def test_selecting_a_name_the_toolset_does_not_hold_raises() -> None:
    with pytest.raises(tools.UnknownToolError):
        tools.TOOLS.select("blackboard_ack")


# Running what the model asked for


def test_a_write_reaches_the_board() -> None:
    board = a_board()
    result = tools.run(
        board, "blackboard_write", {"level": "findings", "content": {"host": "web-3"}}
    )
    assert not result.is_error
    assert json.loads(result.content)["sequence"] == 2
    assert [c.content for c in board.read_level("findings")] == [{"host": "web-3"}]


def test_a_read_carries_the_sequence_to_continue_from() -> None:
    board = a_board()
    board.write("signals", {"host": "web-3"})
    board.write("signals", {"host": "web-4"})
    result = tools.run(board, "blackboard_read_level", {"level": "signals"})
    body = json.loads(result.content)
    assert [c["content"] for c in body["contributions"]] == [
        {"host": "web-3"},
        {"host": "web-4"},
    ]
    assert body["next_from_sequence"] == 4


def test_reading_the_regions_says_which_kind_each_one_is() -> None:
    result = tools.run(a_board(), "blackboard_read_regions", {})
    body = json.loads(result.content)
    assert body["regions"] == [
        {"name": "findings", "kind": "level"},
        {"name": "severity", "kind": "premise"},
        {"name": "signals", "kind": "level"},
    ]


def test_reading_a_premise_carries_the_version_a_write_needs() -> None:
    result = tools.run(a_board(), "blackboard_read_premise", {"premise": "severity"})
    assert json.loads(result.content) == {"value": "unknown", "version": 1}


def test_reading_the_board_covers_every_region() -> None:
    board = a_board()
    board.write("signals", "a")
    board.write("findings", "b")
    body = json.loads(tools.run(board, "blackboard_read_board", {}).content)
    assert [c["region"] for c in body["changes"]] == ["severity", "signals", "findings"]


def test_setting_a_premise_under_the_current_version_lands() -> None:
    board = a_board()
    result = tools.run(
        board,
        "blackboard_set_premise",
        {"premise": "severity", "value": "high", "expected_version": 1},
    )
    assert not result.is_error
    assert board.read_premise("severity").value == "high"


# What comes back when the model gets it wrong


def test_a_region_the_board_does_not_hold_comes_back_naming_the_ones_it_does() -> None:
    """The model corrects itself rather than repeating the call."""
    result = tools.run(a_board(), "blackboard_read_level", {"level": "signal"})
    assert result.is_error
    assert "signal" in result.content
    assert "signals" in result.content
    assert "findings" in result.content


def test_naming_a_premise_where_a_level_belongs_says_which_kind_it_is() -> None:
    result = tools.run(a_board(), "blackboard_read_level", {"level": "severity"})
    assert result.is_error
    assert "premise" in result.content


def test_a_missing_argument_comes_back_as_the_argument_it_needs() -> None:
    result = tools.run(a_board(), "blackboard_write", {"level": "findings"})
    assert result.is_error
    assert "content" in result.content


def test_an_argument_of_the_wrong_type_comes_back_as_the_type_it_takes() -> None:
    result = tools.run(
        a_board(), "blackboard_read_level", {"level": "signals", "from_sequence": "two"}
    )
    assert result.is_error
    assert "from_sequence" in result.content
    assert "integer" in result.content


def test_a_malformed_call_never_reaches_the_board() -> None:
    board = a_board()
    tools.run(board, "blackboard_write", {"level": "findings"})
    assert board.read_board() == board.read_board()
    assert list(board.read_level("findings")) == []


def test_a_boolean_is_not_an_integer() -> None:
    result = tools.run(
        a_board(),
        "blackboard_set_premise",
        {"premise": "severity", "value": "high", "expected_version": True},
    )
    assert result.is_error


def test_an_argument_the_schema_does_not_name_is_ignored() -> None:
    result = tools.run(
        a_board(), "blackboard_read_premise", {"premise": "severity", "limit": 5}
    )
    assert not result.is_error


def test_a_name_the_toolset_does_not_hold_raises() -> None:
    """The caller routes its own tools; a name this set does not own is theirs."""
    with pytest.raises(tools.UnknownToolError):
        tools.run(a_board(), "search", {})


# What comes back when the run refuses


def test_a_rejected_write_is_a_result_the_model_reads_rather_than_an_error() -> None:
    board = a_board(admission_rule=lambda proposed, reader: Reject("out of window"))
    result = tools.run(board, "blackboard_write", {"level": "findings", "content": "x"})
    assert not result.is_error
    assert "out of window" in result.content
    assert "admission" in result.content


def test_a_write_outside_what_the_agent_declared_says_so() -> None:
    board = a_run(
        agents=[Agent(name="triage", notify=lambda n: None, writes_to=["signals"])]
    ).as_agent("triage")
    result = tools.run(board, "blackboard_write", {"level": "findings", "content": "x"})
    assert not result.is_error
    assert "not_permitted" in result.content


def test_a_conflict_carries_the_current_version_so_the_model_can_decide_again() -> None:
    control = a_run()
    board = control.as_agent("triage")
    control.set_premise("severity", "low", 1, writer="other")
    result = tools.run(
        board,
        "blackboard_set_premise",
        {"premise": "severity", "value": "high", "expected_version": 1},
    )
    assert not result.is_error
    body = json.loads(result.content)
    assert body["conflict"]["current_version"] == 2


def test_a_closed_run_says_the_run_has_closed() -> None:
    control = a_run()
    board = control.as_agent("triage")
    control.abort("the operator stopped it")
    result = tools.run(board, "blackboard_write", {"level": "findings", "content": "x"})
    assert "closed" in result.content.lower()


# The call id becomes the idempotency key


def test_the_same_call_id_writes_once() -> None:
    board = a_board()
    first = tools.run(
        board,
        "blackboard_write",
        {"level": "findings", "content": "x"},
        call_id="toolu_01",
    )
    second = tools.run(
        board,
        "blackboard_write",
        {"level": "findings", "content": "x"},
        call_id="toolu_01",
    )
    assert len(board.read_level("findings")) == 1
    assert (
        json.loads(first.content)["sequence"] == json.loads(second.content)["sequence"]
    )
    assert json.loads(second.content)["repeated"] is True


def test_a_call_with_no_id_is_written_every_time() -> None:
    board = a_board()
    tools.run(board, "blackboard_write", {"level": "findings", "content": "x"})
    tools.run(board, "blackboard_write", {"level": "findings", "content": "x"})
    assert len(board.read_level("findings")) == 2


def test_a_read_ignores_the_call_id() -> None:
    board = a_board()
    result = tools.run(board, "blackboard_read_regions", {}, call_id="toolu_01")
    assert not result.is_error


# A result that would not fit


def test_a_long_read_is_cut_and_says_how_much_it_left_out() -> None:
    board = a_board()
    for index in range(400):
        board.write("signals", {"index": index, "padding": "x" * 200})
    result = tools.run(board, "blackboard_read_level", {"level": "signals"})
    body = json.loads(result.content)
    assert len(result.content) <= tools.MAX_RESULT_BYTES
    assert body["omitted"] > 0
    assert len(body["contributions"]) + body["omitted"] == 400
    assert body["next_from_sequence"] == body["contributions"][-1]["sequence"] + 1


def test_a_short_read_says_nothing_about_omission() -> None:
    board = a_board()
    board.write("signals", "x")
    body = json.loads(
        tools.run(board, "blackboard_read_level", {"level": "signals"}).content
    )
    assert "omitted" not in body


def test_nested_content_reaches_the_model_as_it_was_written() -> None:
    """Every store refuses what JSON cannot carry, so a read renders as it is."""
    board = a_board()
    written = {"findings": ["oom"], "counts": [1, 2, 3], "nested": {"a": None}}
    board.write("signals", written)
    result = tools.run(board, "blackboard_read_level", {"level": "signals"})
    assert json.loads(result.content)["contributions"][0]["content"] == written


# What the caller gets besides the text


def test_a_result_carries_the_value_the_board_returned() -> None:
    from blackboard import Written

    board = a_board()
    result = tools.run(board, "blackboard_write", {"level": "findings", "content": "x"})
    assert isinstance(result.value, Written)
    assert result.value.sequence == 2


# The projection depends on nothing outside the base install


def test_the_module_imports_without_an_extra() -> None:
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable,
            "-c",
            "import blackboard.tools; blackboard.tools.for_anthropic()",
        ],
        check=True,
    )


# A loop, with the model scripted rather than called


class ScriptedModel:
    """A model API's answers, written down instead of asked for.

    Each turn is a list of calls, and what came back for the previous turn is
    kept so a test can assert what the model was told.
    """

    def __init__(self, turns: list[list[tuple[str, str, dict[str, Any]]]]) -> None:
        self._turns = turns
        self.told: list[tools.ToolResult] = []

    def __call__(
        self, offered: list[dict[str, Any]]
    ) -> list[tuple[str, str, dict[str, Any]]]:
        assert offered, "a turn is taken with tools offered"
        return self._turns.pop(0) if self._turns else []


def answer_with(board: AgentBoard, model: ScriptedModel) -> None:
    """The loop this module exists for, with the provider SDK left out.

    A caller sends the offer, runs the calls this toolset owns, hands the
    results back, and stops when the model asks for nothing further.
    """
    offered = [
        *tools.for_anthropic(),
        {"name": "search", "description": "...", "input_schema": {}},
    ]
    while True:
        calls = model(offered)
        if not calls:
            return
        for call_id, name, arguments in calls:
            if not tools.TOOLS.owns(name):
                continue  # the caller's own tool, routed by the caller
            model.told.append(tools.run(board, name, arguments, call_id=call_id))


def test_a_model_that_names_a_region_wrongly_is_told_the_ones_that_exist() -> None:
    """The self-correction the projection is built for, over two turns."""
    board = a_board()
    model = ScriptedModel(
        [
            [("toolu_01", "blackboard_write", {"level": "finding", "content": "a"})],
            [("toolu_02", "blackboard_write", {"level": "findings", "content": "a"})],
        ]
    )
    answer_with(board, model)

    refused, landed = model.told
    assert refused.is_error and "'findings'" in refused.content
    assert not landed.is_error
    assert [c.content for c in board.read_level("findings")] == ["a"]


def test_a_call_the_toolset_does_not_own_is_left_to_the_caller() -> None:
    board = a_board()
    model = ScriptedModel([[("toolu_01", "search", {"q": "web-3"})]])
    answer_with(board, model)
    assert model.told == []


def test_the_same_loop_runs_against_a_board_reached_over_http() -> None:
    """`tools.run` takes the protocol, so the deployment does not reach it."""
    pytest.importorskip("httpx")
    from blackboard.agent import BoardClient
    from blackboard.server import BoardService, Request

    control = a_run()
    service = BoardService(lambda board_id: control)

    def handler(request: Any) -> Any:
        import httpx

        answer = service.handle(
            Request(
                method=request.method,
                path=request.url.path,
                query=dict(request.url.params),
                body=json.loads(request.content) if request.content else None,
            )
        )
        return httpx.Response(answer.status, json=answer.body)

    import httpx

    transport = httpx.MockTransport(handler)
    model = ScriptedModel(
        [[("toolu_01", "blackboard_write", {"level": "findings", "content": "a"})]]
    )
    with BoardClient(
        base_url="http://blackboard.test",
        board_id=BOARD,
        agent="triage",
        http_client=httpx.Client(transport=transport),
    ) as board:
        answer_with(board, model)

    assert not model.told[0].is_error
    assert [c.content for c in control.reader.read_level("findings")] == ["a"]
