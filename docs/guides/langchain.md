# Use the board inside LangChain

LangChain runs the loop around a model and calls the tools the model asks for.
A board joins that loop as tools, which is what an agent whose expertise is a
language model needs from this library: `blackboard.tools` supplies the
schemas and runs the calls, and LangChain supplies everything around them.

Nothing in this library imports LangChain, and installing this library does not
install it.

## Turning the descriptors into LangChain tools

`ToolDescriptor` carries the name, the description and the schema. LangChain's
`StructuredTool` carries the same three and a function to run.

```python
from typing import Annotated, Any

from langchain_core.tools import InjectedToolCallId, StructuredTool
from pydantic import Field, create_model as pydantic_model

from blackboard import tools

PYTHON_TYPE: dict[str | None, Any] = {"string": str, "integer": int}


def as_langchain_tool(board, descriptor):
    """Builds the LangChain tool for one of this library's descriptors."""
    schema = descriptor.input_schema
    fields: dict[str, Any] = {}
    for name, spec in schema["properties"].items():
        annotation = PYTHON_TYPE.get(spec.get("type"), Any)
        needed = name in schema["required"]
        fields[name] = (
            annotation,
            Field(... if needed else None, description=spec.get("description")),
        )
    fields["call_id"] = (Annotated[str, InjectedToolCallId], Field(None))

    def call(call_id: str, **arguments: Any) -> str:
        given = {k: v for k, v in arguments.items() if v is not None}
        return tools.run(board, descriptor.name, given, call_id=call_id).content

    return StructuredTool(
        name=descriptor.name,
        description=descriptor.description,
        args_schema=pydantic_model(descriptor.name, **fields),
        func=call,
    )


board = model.control.as_agent("triage")
board_tools = [as_langchain_tool(board, d) for d in tools.TOOLS]
```

`board_tools` goes wherever LangChain takes tools, beside your own.

```python
agent = create_agent(llm, board_tools + my_own_tools)
```

## What the model is shown

Each tool offers the parameters of the method it calls, and nothing else.

| Tool | What the model may send |
| --- | --- |
| `blackboard_read_regions` | nothing |
| `blackboard_read_level` | `level`, `from_sequence` |
| `blackboard_read_premise` | `premise` |
| `blackboard_read_board` | `from_sequence` |
| `blackboard_write` | `level`, `content` |
| `blackboard_set_premise` | `premise`, `value`, `expected_version` |

The agent's name is absent because the `AgentBoard` you bound carries it.
`idempotency_key` is absent because the call identifier fills it, and `call_id`
is absent from what the model sees because LangChain injects it.

## Why the call identifier is injected

`InjectedToolCallId` is what makes a repeated tool call write once. LangChain
fills that field from the identifier the model API gave the call, `tools.run`
passes it as the write's idempotency key, and a second execution of the same
call returns the first write rather than adding a second.

```python
{"sequence": 2}  # the first execution
{"sequence": 2, "repeated": true}  # the same call, executed again
```

LangChain fills an injected field only in a schema it derived from a Pydantic
model, which is why `as_langchain_tool` builds one rather than passing
`descriptor.input_schema` as it stands. Passing the schema directly works and
offers the model the same parameters; what it loses is the identifier, so each
execution writes again.

## What the model is told when it is wrong

`tools.run` answers rather than raising, so a mistake reaches the model as the
tool's result and the loop continues.

```
no region is declared with the name 'finding'. This board holds the
levels 'findings', 'signals' and the premises 'severity'.
```

A write the run refused arrives the same way, carrying the cause and the
reason, because the model asked correctly and the run declined. [Let a model
decide](deciding-with-a-model.md#a-refusal-is-not-a-mistake) covers what each
outcome looks like.

## Offering a model less than everything

Build from a subset, and the tools the model never sees are the ones it cannot
call.

```python
reads = [as_langchain_tool(board, d) for d in tools.TOOLS.read_only()]
```

What an agent may write is still settled by its `writes_to` declaration and by
the admission rule, which hold wherever a write came from.

## Acknowledging

Acknowledgment is not among the tools, so acknowledge after LangChain's loop
returns.

```python
result = agent.invoke({"messages": [("user", brief(notification))]})
board.ack(notification.notification_id)
```

## Binding the schemas to a model directly

Code that calls `bind_tools` on a chat model rather than building an agent
takes the rendered schemas as they are, because LangChain converts a plain tool
dictionary itself.

```python
llm.bind_tools(tools.for_openai())
```

That path offers the model the same tools and leaves you to run the calls with
`tools.run`, which is [the loop the other guide
shows](deciding-with-a-model.md#the-loop-once).

## Versions

The code above was run against `langchain-core` 1.6.1. LangChain names its own
tool interfaces, and a later release may name them differently.
