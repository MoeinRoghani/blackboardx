# Let a model decide

An agent's five steps do not change when a language model supplies its
expertise. It is notified, it reads the board, it decides what to add, it
writes, and it acknowledges. Only the third step moves: instead of an
algorithm in the agent's own code deciding, a model is asked, and it is asked
by being offered the board's reads and writes as tools it may call.

`blackboard.tools` renders those reads and writes into what a model API
accepts, and runs what the model asks for.

## The model asks, and your code runs

A model API takes a schema for each tool and answers with a request to call
one by name. It does not run anything. The model has no connection to your
board and no handle on the `AgentBoard` your process holds, so a call the
model asks for reaches the board when your code makes it.

That is the whole loop, and it has four steps.

1. You offer the schemas with the messages.
2. The model answers with the calls it wants.
3. You run each call and collect what it produced.
4. You hand the results back, and the model answers again.

## Offering the tools

You choose the tools first. Each one is a name this module exports, and
`ToolSet` holds the ones you picked.

```python
from blackboard.tools import ACK, READ_LEVEL, READ_PREMISE, WRITE, ToolSet

board_tools = ToolSet([READ_LEVEL, READ_PREMISE, WRITE, ACK])
```

`for_anthropic` and `for_openai` render that set into each provider's shape.
Both return a plain list, so your own tools join them.

```python
offered = board_tools.for_anthropic() + my_own_tools
```

`tools.ALL` is every tool, for an application that wants them all:
`ToolSet(tools.ALL)`. A single tool renders on its own as well, for code that
assembles the provider payload itself: `WRITE.for_anthropic()`.

Nothing here calls a model API or depends on one, and what these return is a
list of plain dictionaries.

## Running what comes back

`run` takes the board, the name and arguments the model answered with, and the
identifier that API gave the call. It is a method on the set you offered, so
the tools you rendered and the tools you dispatch are the same tools.

```python
outcome = board_tools.run(board, name, arguments, call_id=call_id)
```

`board` is any `AgentBoard`, so the same call serves an agent in the run's
process and an agent reaching the board over HTTP.

`outcome.content` is the text to hand back to the model. `outcome.is_error`
marks a call the model got wrong and can correct. `outcome.value` is what the
board returned, for code that wants that value rather than the text rendered
from it.

A name this toolset does not hold raises `UnknownToolError`, because your own
tools are yours to route. Ask first:

```python
if board_tools.owns(name):
    outcome = board_tools.run(board, name, arguments, call_id=call_id)
```

## The loop, once

```python
from blackboard.tools import READ_LEVEL, READ_PREMISE, WRITE, ToolSet

board_tools = ToolSet([READ_LEVEL, READ_PREMISE, WRITE])
board = model.control.as_agent("triage")
messages = [{"role": "user", "content": brief(notification)}]

while True:
    reply = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=2048,
        tools=board_tools.for_anthropic(),
        messages=messages,
    )
    if reply.stop_reason != "tool_use":
        break
    messages.append({"role": "assistant", "content": reply.content})
    results = []
    for block in reply.content:
        if block.type != "tool_use" or not board_tools.owns(block.name):
            continue
        outcome = board_tools.run(board, block.name, block.input, call_id=block.id)
        results.append(
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": outcome.content,
                "is_error": outcome.is_error,
            }
        )
    messages.append({"role": "user", "content": results})

board.ack(notification.notification_id)
```

The set above leaves `ACK` out, so this loop acknowledges after it ends rather
than letting the model do it. Acknowledging says this agent has stopped working
on the notification, and a model that sends it partway through its own
reasoning tells the run that this agent has finished while it is still working.

`ACK` is exported like every other tool, so an application that wants the model
to decide when it is done puts it in the set and drops the call after the
loop.

## What the model is never asked for

The agent's name appears in no schema, and no method takes it either: the
`AgentBoard` you bound carries it. A model offered these tools writes under the
name you chose and has no way to name another.

Two parameters the methods do take are withheld.

`idempotency_key` is withheld, because `call_id` fills it. A model API gives
every call it asks for an identifier, and passing that identifier means a loop
that sends one call twice writes once. The second answer carries `"repeated": true` and
the same sequence number as the first. Leave `call_id` out and each call
writes again, which is what omitting an idempotency key already means.

`limit` is withheld from `blackboard_read_level` and `blackboard_read_board`,
because the result is bounded here. A read that answers with a list is cut
where its JSON would exceed `tools.MAX_RESULT_BYTES`, and carries `omitted`
with the number of entries left out. Those two reads also carry
`next_from_sequence`, which always moves past what came back, so a model that
follows it reaches the end of a level. `blackboard_read_regions` is cut the
same way and carries no sequence, having none. `blackboard_read_premise`
answers whole, because one value cut in half is a value the model cannot use.

## What comes back when the model gets it wrong

A malformed call changes nothing. Two of the four below are caught before the
board is reached and two are what the board answered, and in both cases what
comes back says what was wrong and `is_error` is true.

| The model sent | What it is told |
| --- | --- |
| A call missing a required argument | Which argument, and which arguments the tool takes |
| An argument of the wrong type | Which argument, the type it takes, and the type that arrived |
| A region name the board does not hold | That no region carries the name, and the levels and premises the board does hold |
| A premise where a level belongs | Which kind that name is |

Naming the regions in the answer is what lets the model correct itself on the
next turn rather than repeat the call.

An argument the schema does not name is ignored rather than refused, because it
changes nothing about the call that is made.

## A refusal is not a mistake

A write the run refused comes back with `is_error` false, because the model
asked correctly and the run said no. Handing it back as an error would tell
the model to change its arguments, and the arguments were right.

```json
{"rejected": {"cause": "admission", "reason": "outside the maintenance window"}}
```

A premise whose version moved comes back the same way, carrying the version
now current, so the model reads the premise again and decides from the value
that is now there.

```json
{"conflict": {"current_version": 4, "reason": "the premise moved since the version this call named. Read it again and decide from the value now there."}}
```

`cause` is one of `admission`, `not_permitted` and `run_closed`, the three
members of `RejectionCause`.

An exception from a store that cannot be reached is left to reach your loop
rather than answered, because no wording of it lets the model proceed. Retrying
or stopping is then a decision your code makes.

## Offering a model less than everything

Build the set from the tools that agent should have.

```python
reviewer = ToolSet([READ_LEVEL, READ_PREMISE])
```

`ToolSet.select` and `ToolSet.read_only` narrow a set you already hold, and
what they return renders and runs exactly as the set it came from does.

```python
ToolSet(tools.ALL).read_only()  # the four reads, no writes
```

Offer a smaller set to an agent whose job is to review rather than to
contribute.
A subset is not a substitute for the run's own rules: what an agent may write
is settled by its `writes_to` declaration and by the admission rule, which hold
wherever the write came from.

## OpenAI, and the Model Context Protocol

`for_openai` carries the same schemas under that API's key names.

```python
offered = board_tools.for_openai()
```

Calls arrive there with the arguments as a JSON string rather than an object,
so parse them before passing them on.

```python
outcome = board_tools.run(
    board, call.function.name, json.loads(call.function.arguments), call_id=call.id
)
```

`ToolSet.definitions` returns the same tools in the shape the Model Context
Protocol defines, which is what a server answering `tools/list` returns.

## An agent deployed on its own

`ToolSet.run` takes `AgentBoard`, and both a run in this process and a client
over HTTP satisfy it, so the loop above runs unchanged against either.

```python
with BoardClient(base_url=..., board_id=..., agent="triage") as board:
    decide_with_a_model(board, notification)
```

The tool schemas do not change between the two, because they are rendered from
the protocol rather than from the deployment.
