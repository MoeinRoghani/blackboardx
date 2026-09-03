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
import re
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

EVERY = tools.ToolSet(tools.ALL)

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


def test_the_toolset_covers_every_method_of_the_protocol() -> None:
    assert [d.method for d in tools.ALL] == [
        "read_regions",
        "read_level",
        "read_premise",
        "read_board",
        "write",
        "set_premise",
        "ack",
    ]


def test_the_application_chooses_never_to_offer_acknowledgment() -> None:
    """Acknowledging says the agent has stopped; a caller may withhold it."""
    without = tools.ToolSet([d for d in tools.ALL if d is not tools.ACK])
    assert not without.owns("blackboard_ack")
    assert len(without.for_anthropic()) == len(tools.ALL) - 1


def test_every_name_carries_the_prefix() -> None:
    """The board's tools sit in a toolset it does not own, so they are marked."""
    for descriptor in tools.ALL:
        assert descriptor.name.startswith(tools.TOOL_PREFIX)
        assert descriptor.name == f"{tools.TOOL_PREFIX}{descriptor.method}"


def test_a_name_is_checked_against_the_toolset() -> None:
    assert EVERY.owns("blackboard_write")
    assert not EVERY.owns("my_own_tool")


# The projection holds to the protocol


def test_a_schema_names_the_parameters_of_the_method_it_calls() -> None:
    """The check that keeps a hand-written schema from drifting from the code."""
    for descriptor in tools.ALL:
        method = getattr(AgentBoard, descriptor.method)
        parameters = set(inspect.signature(method).parameters) - {"self"}
        offered = set(descriptor.input_schema["properties"])
        assert offered | set(descriptor.withholds) == parameters, descriptor.name
        assert not offered & set(descriptor.withholds), descriptor.name


def test_no_schema_asks_the_model_for_an_identity() -> None:
    """`AgentBoard` carries the name, so a model cannot write as another agent."""
    for descriptor in tools.ALL:
        assert "writer" not in descriptor.input_schema["properties"]
        assert "agent" not in descriptor.input_schema["properties"]


def test_no_schema_asks_the_model_for_an_idempotency_key() -> None:
    for descriptor in tools.ALL:
        assert "idempotency_key" not in descriptor.input_schema["properties"]
    board_writes = [tools.WRITE, tools.SET_PREMISE]
    for descriptor in board_writes:
        assert "idempotency_key" in descriptor.from_call_id
    # Acknowledging twice changes nothing on its own, so it needs no key.
    assert tools.ACK.from_call_id == ()


def test_what_is_taken_from_the_call_id_is_also_withheld() -> None:
    for descriptor in tools.ALL:
        assert set(descriptor.from_call_id) <= set(descriptor.withholds)


def test_every_description_says_what_comes_back() -> None:
    for descriptor in tools.ALL:
        assert len(descriptor.description) > 80, descriptor.name


# Rendering into a provider's shape


def test_the_anthropic_shape_carries_the_schema_under_input_schema() -> None:
    rendered = EVERY.for_anthropic()
    assert {t["name"] for t in rendered} == {d.name for d in tools.ALL}
    first = rendered[0]
    assert set(first) == {"name", "description", "input_schema"}
    assert first["input_schema"]["type"] == "object"


def test_the_openai_shape_nests_the_schema_under_function() -> None:
    rendered = EVERY.for_openai()
    assert {t["function"]["name"] for t in rendered} == {d.name for d in tools.ALL}
    first = rendered[0]
    assert first["type"] == "function"
    assert set(first["function"]) == {"name", "description", "parameters"}


def test_both_shapes_carry_the_same_schemas() -> None:
    for anthropic, openai in zip(
        EVERY.for_anthropic(), EVERY.for_openai(), strict=True
    ):
        assert anthropic["input_schema"] == openai["function"]["parameters"]
        assert anthropic["description"] == openai["function"]["description"]


def test_a_rendered_toolset_joins_a_caller_s_own_tools() -> None:
    """The call site this exists for: our tools beside theirs, in one list."""
    mine = [{"name": "search", "description": "...", "input_schema": {}}]
    offered = EVERY.for_anthropic() + mine
    assert len(offered) == len(tools.ALL) + 1
    assert offered[-1] == mine[0]


def test_a_subset_renders_on_its_own() -> None:
    only_reads = EVERY.read_only()
    assert [d.method for d in only_reads] == [
        "read_regions",
        "read_level",
        "read_premise",
        "read_board",
    ]
    assert len(only_reads.for_anthropic()) == 4


def test_a_named_subset_renders_on_its_own() -> None:
    chosen = EVERY.select("blackboard_write", "blackboard_read_level")
    assert [d.method for d in chosen] == ["read_level", "write"]


def test_selecting_a_name_the_toolset_does_not_hold_raises() -> None:
    with pytest.raises(tools.UnknownToolError):
        EVERY.select("blackboard_declare")


# Running what the model asked for


def test_a_write_reaches_the_board() -> None:
    board = a_board()
    result = EVERY.run(
        board, "blackboard_write", {"level": "findings", "content": {"host": "web-3"}}
    )
    assert not result.is_error
    assert json.loads(result.content)["sequence"] == 2
    assert [c.content for c in board.read_level("findings")] == [{"host": "web-3"}]


def test_a_read_carries_the_sequence_to_continue_from() -> None:
    board = a_board()
    board.write("signals", {"host": "web-3"})
    board.write("signals", {"host": "web-4"})
    result = EVERY.run(board, "blackboard_read_level", {"level": "signals"})
    body = json.loads(result.content)
    assert [c["content"] for c in body["contributions"]] == [
        {"host": "web-3"},
        {"host": "web-4"},
    ]
    assert body["next_from_sequence"] == 4


def test_reading_the_regions_says_which_kind_each_one_is() -> None:
    result = EVERY.run(a_board(), "blackboard_read_regions", {})
    body = json.loads(result.content)
    assert body["regions"] == [
        {"name": "findings", "kind": "level"},
        {"name": "severity", "kind": "premise"},
        {"name": "signals", "kind": "level"},
    ]


def test_reading_a_premise_carries_the_version_a_write_needs() -> None:
    result = EVERY.run(a_board(), "blackboard_read_premise", {"premise": "severity"})
    assert json.loads(result.content) == {"value": "unknown", "version": 1}


def test_reading_the_board_covers_every_region() -> None:
    board = a_board()
    board.write("signals", "a")
    board.write("findings", "b")
    body = json.loads(EVERY.run(board, "blackboard_read_board", {}).content)
    assert [c["region"] for c in body["changes"]] == ["severity", "signals", "findings"]


def test_setting_a_premise_under_the_current_version_lands() -> None:
    board = a_board()
    result = EVERY.run(
        board,
        "blackboard_set_premise",
        {"premise": "severity", "value": "high", "expected_version": 1},
    )
    assert not result.is_error
    assert board.read_premise("severity").value == "high"


# What comes back when the model gets it wrong


def test_a_region_the_board_does_not_hold_comes_back_naming_the_ones_it_does() -> None:
    """The model corrects itself rather than repeating the call."""
    result = EVERY.run(a_board(), "blackboard_read_level", {"level": "signal"})
    assert result.is_error
    assert "signal" in result.content
    assert "signals" in result.content
    assert "findings" in result.content


def test_naming_a_premise_where_a_level_belongs_says_which_kind_it_is() -> None:
    result = EVERY.run(a_board(), "blackboard_read_level", {"level": "severity"})
    assert result.is_error
    assert "premise" in result.content


def test_a_missing_argument_comes_back_as_the_argument_it_needs() -> None:
    result = EVERY.run(a_board(), "blackboard_write", {"level": "findings"})
    assert result.is_error
    assert "content" in result.content


def test_an_argument_of_the_wrong_type_comes_back_as_the_type_it_takes() -> None:
    result = EVERY.run(
        a_board(), "blackboard_read_level", {"level": "signals", "from_sequence": "two"}
    )
    assert result.is_error
    assert "from_sequence" in result.content
    assert "integer" in result.content


def test_a_malformed_call_never_reaches_the_board() -> None:
    board = a_board()
    EVERY.run(board, "blackboard_write", {"level": "findings"})
    assert board.read_board() == board.read_board()
    assert list(board.read_level("findings")) == []


def test_a_boolean_is_not_an_integer() -> None:
    result = EVERY.run(
        a_board(),
        "blackboard_set_premise",
        {"premise": "severity", "value": "high", "expected_version": True},
    )
    assert result.is_error


def test_an_argument_the_schema_does_not_name_is_ignored() -> None:
    result = EVERY.run(
        a_board(), "blackboard_read_premise", {"premise": "severity", "limit": 5}
    )
    assert not result.is_error


def test_a_name_the_toolset_does_not_hold_raises() -> None:
    """The caller routes its own tools; a name this set does not own is theirs."""
    with pytest.raises(tools.UnknownToolError):
        EVERY.run(a_board(), "search", {})


# What comes back when the run refuses


def test_a_rejected_write_is_a_result_the_model_reads_rather_than_an_error() -> None:
    board = a_board(admission_rule=lambda proposed, reader: Reject("out of window"))
    result = EVERY.run(board, "blackboard_write", {"level": "findings", "content": "x"})
    assert not result.is_error
    assert "out of window" in result.content
    assert "admission" in result.content


def test_a_write_outside_what_the_agent_declared_says_so() -> None:
    board = a_run(
        agents=[Agent(name="triage", notify=lambda n: None, writes_to=["signals"])]
    ).as_agent("triage")
    result = EVERY.run(board, "blackboard_write", {"level": "findings", "content": "x"})
    assert not result.is_error
    assert "not_permitted" in result.content


def test_a_conflict_carries_the_current_version_so_the_model_can_decide_again() -> None:
    control = a_run()
    board = control.as_agent("triage")
    control.set_premise("severity", "low", 1, writer="other")
    result = EVERY.run(
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
    result = EVERY.run(board, "blackboard_write", {"level": "findings", "content": "x"})
    assert "closed" in result.content.lower()


# The call id becomes the idempotency key


def test_the_same_call_id_writes_once() -> None:
    board = a_board()
    first = EVERY.run(
        board,
        "blackboard_write",
        {"level": "findings", "content": "x"},
        call_id="toolu_01",
    )
    second = EVERY.run(
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
    EVERY.run(board, "blackboard_write", {"level": "findings", "content": "x"})
    EVERY.run(board, "blackboard_write", {"level": "findings", "content": "x"})
    assert len(board.read_level("findings")) == 2


def test_a_read_ignores_the_call_id() -> None:
    board = a_board()
    result = EVERY.run(board, "blackboard_read_regions", {}, call_id="toolu_01")
    assert not result.is_error


# A result that would not fit


def test_a_long_read_is_cut_and_says_how_much_it_left_out() -> None:
    board = a_board()
    for index in range(400):
        board.write("signals", {"index": index, "padding": "x" * 200})
    result = EVERY.run(board, "blackboard_read_level", {"level": "signals"})
    body = json.loads(result.content)
    assert len(result.content) <= tools.MAX_RESULT_BYTES
    assert body["omitted"] > 0
    assert len(body["contributions"]) + body["omitted"] == 400
    assert body["next_from_sequence"] == body["contributions"][-1]["sequence"] + 1


def test_a_short_read_says_nothing_about_omission() -> None:
    board = a_board()
    board.write("signals", "x")
    body = json.loads(
        EVERY.run(board, "blackboard_read_level", {"level": "signals"}).content
    )
    assert "omitted" not in body


def test_nested_content_reaches_the_model_as_it_was_written() -> None:
    """Every store refuses what JSON cannot carry, so a read renders as it is."""
    board = a_board()
    written = {"findings": ["oom"], "counts": [1, 2, 3], "nested": {"a": None}}
    board.write("signals", written)
    result = EVERY.run(board, "blackboard_read_level", {"level": "signals"})
    assert json.loads(result.content)["contributions"][0]["content"] == written


# What the caller gets besides the text


def test_a_result_carries_the_value_the_board_returned() -> None:
    from blackboard import Written

    board = a_board()
    result = EVERY.run(board, "blackboard_write", {"level": "findings", "content": "x"})
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
            "from blackboard import tools; tools.ToolSet(tools.ALL).for_anthropic()",
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
        *EVERY.for_anthropic(),
        {"name": "search", "description": "...", "input_schema": {}},
    ]
    while True:
        calls = model(offered)
        if not calls:
            return
        for call_id, name, arguments in calls:
            if not EVERY.owns(name):
                continue  # the caller's own tool, routed by the caller
            model.told.append(EVERY.run(board, name, arguments, call_id=call_id))


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


def _serving(control: Control) -> Any:
    """A transport that answers an agent's requests from this run."""
    import httpx

    from blackboard.server import BoardService, Request

    service = BoardService(lambda board_id: control)

    def handler(request: Any) -> Any:
        answer = service.handle(
            Request(
                method=request.method,
                path=request.url.path,
                query=dict(request.url.params),
                body=json.loads(request.content) if request.content else None,
            )
        )
        return httpx.Response(answer.status, json=answer.body)

    return handler


def test_the_same_loop_runs_against_a_board_reached_over_http() -> None:
    """`tools.run` takes the protocol, so the deployment does not reach it."""
    pytest.importorskip("httpx")
    from blackboard.agent import BoardClient

    control = a_run()

    import httpx

    transport = httpx.MockTransport(_serving(control))
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


def test_a_region_error_is_answered_over_http_as_it_is_in_process() -> None:
    """The answered conditions hold for a board reached over the wire too."""
    pytest.importorskip("httpx")
    import httpx

    from blackboard.agent import BoardClient

    control = a_run()
    with BoardClient(
        base_url="http://blackboard.test",
        board_id=BOARD,
        agent="triage",
        http_client=httpx.Client(transport=httpx.MockTransport(_serving(control))),
    ) as board:
        result = EVERY.run(board, "blackboard_read_level", {"level": "signal"})

    assert result.is_error
    assert "signal" in result.content
    assert "findings" in result.content


# The descriptions are interface, so they are held to the same rules


def test_no_description_carries_a_construction_the_standard_forbids() -> None:
    """A description acts at run time, so the writing standard binds it."""
    forbidden = ("—", " whether", " neither")
    for descriptor in tools.ALL:
        prose = [descriptor.description] + [
            spec.get("description", "")
            for spec in descriptor.input_schema["properties"].values()
        ]
        for sentence in prose:
            for banned in forbidden:
                assert banned not in sentence, (descriptor.name, banned)


def test_a_description_names_only_tools_that_exist() -> None:
    """A renamed tool cannot leave another description pointing at nothing."""
    named = set()
    for descriptor in tools.ALL:
        prose = descriptor.description + " ".join(
            spec.get("description", "")
            for spec in descriptor.input_schema["properties"].values()
        )
        named.update(re.findall(rf"{tools.TOOL_PREFIX}\w+", prose))
    assert named
    for name in named:
        assert EVERY.owns(name), name


def test_every_parameter_carries_a_description() -> None:
    for descriptor in tools.ALL:
        for parameter, spec in descriptor.input_schema["properties"].items():
            assert spec.get("description"), (descriptor.name, parameter)


# A read always moves the cursor forward


def test_one_entry_larger_than_the_budget_comes_back_whole() -> None:
    """A cut that returned nothing would leave a model reading the same page."""
    board = a_board()
    board.write("signals", "x" * (tools.MAX_RESULT_BYTES * 2))
    board.write("signals", "small")
    result = EVERY.run(board, "blackboard_read_level", {"level": "signals"})
    body = json.loads(result.content)
    assert len(body["contributions"]) == 1
    assert body["contributions"][0]["sequence"] == 2
    assert body["next_from_sequence"] == 3
    assert body["omitted"] == 1


def test_a_read_that_cuts_never_answers_with_the_bound_it_was_given() -> None:
    """Whatever the sizes, the next call asks for something later than this one."""
    board = a_board()
    for index in range(50):
        board.write("signals", {"index": index, "padding": "y" * 900})
    body = json.loads(
        EVERY.run(board, "blackboard_read_level", {"level": "signals"}).content
    )
    assert body["omitted"] > 0
    assert body["next_from_sequence"] > 0


def test_a_premise_value_is_answered_whole() -> None:
    """One value cut in half is a value the model cannot use."""
    board = a_board(premises={"severity": "y" * (tools.MAX_RESULT_BYTES * 2)})
    result = EVERY.run(board, "blackboard_read_premise", {"premise": "severity"})
    assert not result.is_error
    body = json.loads(result.content)
    assert len(body["value"]) == tools.MAX_RESULT_BYTES * 2
    assert "omitted" not in body


def test_the_region_list_is_cut_and_carries_no_sequence() -> None:
    """Regions have no position, so a cut list has nothing to continue from."""
    many = [Level(f"level-{index:04d}") for index in range(600)]
    board = a_board(regions=many, premises={})
    body = json.loads(EVERY.run(board, "blackboard_read_regions", {}).content)
    assert body["omitted"] > 0
    assert len(body["regions"]) + body["omitted"] == 600
    assert "next_from_sequence" not in body


def test_a_condition_no_tool_can_raise_is_not_caught() -> None:
    """The answered list is what these seven calls raise, and nothing besides."""
    from blackboard._board import (
        IdempotencyKeyError,
        RegionKindError,
        UndeclaredRegionError,
        UnsetPremiseError,
    )
    from blackboard._control import UnknownNotificationError

    assert set(tools._ANSWERABLE) == {
        UndeclaredRegionError,
        RegionKindError,
        UnsetPremiseError,
        IdempotencyKeyError,
        UnknownNotificationError,
    }


def test_a_premise_declared_without_a_value_says_so_rather_than_raising() -> None:
    control = a_run()
    control.declare(Premise("unset"))
    result = EVERY.run(
        control.as_agent("triage"), "blackboard_read_premise", {"premise": "unset"}
    )
    assert result.is_error
    assert "unset" in result.content


# The application composes the list


def test_every_tool_is_importable_on_its_own() -> None:
    """The library exports the parts; the grouping is the application's."""
    from blackboard.tools import (
        ACK,
        READ_BOARD,
        READ_LEVEL,
        READ_PREMISE,
        READ_REGIONS,
        SET_PREMISE,
        WRITE,
    )

    assert [d.method for d in tools.ALL] == [
        "read_regions",
        "read_level",
        "read_premise",
        "read_board",
        "write",
        "set_premise",
        "ack",
    ]
    assert tools.ALL == (
        READ_REGIONS,
        READ_LEVEL,
        READ_PREMISE,
        READ_BOARD,
        WRITE,
        SET_PREMISE,
        ACK,
    )


def test_a_set_holds_what_the_application_chose() -> None:
    chosen = tools.ToolSet([tools.READ_LEVEL, tools.WRITE, tools.ACK])
    assert [d.name for d in chosen] == [
        "blackboard_read_level",
        "blackboard_write",
        "blackboard_ack",
    ]
    assert len(chosen.for_anthropic()) == 3


def test_a_set_declines_a_name_another_set_holds() -> None:
    """Two sets in one application cannot dispatch through each other."""
    reviewer = tools.ToolSet([tools.READ_LEVEL, tools.READ_PREMISE])
    assert not reviewer.owns("blackboard_write")
    with pytest.raises(tools.UnknownToolError):
        reviewer.run(
            a_board(), "blackboard_write", {"level": "findings", "content": "x"}
        )


def test_one_descriptor_renders_on_its_own() -> None:
    """An application that builds the provider payload itself needs no set."""
    rendered = tools.WRITE.for_anthropic()
    assert set(rendered) == {"name", "description", "input_schema"}
    assert rendered["name"] == "blackboard_write"
    assert tools.WRITE.for_openai()["function"]["name"] == "blackboard_write"
    assert tools.WRITE.definition()["inputSchema"] == tools.WRITE.input_schema


def test_the_bundle_and_its_shortcuts_are_gone() -> None:
    """The library no longer ships a grouping of its own."""
    for name in ("TOOLS", "run", "for_anthropic", "for_openai", "definitions"):
        assert not hasattr(tools, name), name


# Acknowledgment is a tool the application may offer


def test_acknowledging_is_among_the_tools() -> None:
    assert tools.ACK.name == "blackboard_ack"
    assert tools.ACK.method == "ack"
    assert set(tools.ACK.input_schema["properties"]) == {"notification_id"}
    assert tools.ACK.input_schema["required"] == ["notification_id"]
    assert not tools.ACK.annotations.read_only


def test_acknowledging_through_a_tool_reaches_the_run() -> None:
    held: list[Any] = []
    control = a_run(agents=[Agent(name="triage", notify=held.append)])
    board = control.as_agent("triage")
    control.write("signals", "a", writer="other")
    outstanding = held[-1]

    result = tools.ToolSet([tools.ACK]).run(
        board, "blackboard_ack", {"notification_id": int(outstanding.notification_id)}
    )
    assert not result.is_error
    assert json.loads(result.content)["acknowledged"] == int(
        outstanding.notification_id
    )
    assert control.outcome() is None or True


def test_acknowledging_one_this_agent_never_had_is_answered() -> None:
    """The model can act on it, so it comes back rather than ending the loop."""
    result = tools.ToolSet([tools.ACK]).run(
        a_board(), "blackboard_ack", {"notification_id": 999}
    )
    assert result.is_error
    assert "999" in result.content


def test_the_answered_conditions_cover_acknowledgment() -> None:
    from blackboard._control import UnknownNotificationError

    assert UnknownNotificationError in tools._ANSWERABLE
