# ADR 0021: What a model is offered is written, not derived from the signature

Date: 2026-09-03

## Status

Accepted.

## Context

`AgentBoard` is what an agent body is written against, and an application whose agent decides its own work by an algorithm calls those methods. An application that puts a language model in the position of deciding cannot hand the methods over, because a model API takes a schema for each thing it may call and answers with a request to call one by name.

These frameworks will derive such a schema from a function's signature, its type hints, and its docstring, and that is the path an application takes by default. Derivation over this surface produces a defective tool, in four ways.

`content` and `value` are typed `object`, because a board carries any value JSON can. Derivation renders that as a property with no type and no description, which tells the model nothing about what belongs there. A written schema leaves the type open too, because the board does carry any value JSON can, and says so in the description.

`idempotency_key` becomes a parameter the model fills in. A model that leaves it out loses the protection it exists for, and a model that invents a value gives one that is not guaranteed to be the same on a second attempt, which is the one thing an idempotency key must be.

The docstrings are written for a reader of the API reference. "Proposes a contribution to a level, as this object's agent" is the sentence that page wants. It does not say when to call the tool, that the content is stored as JSON, or what a refusal means.

The outcomes are values and exceptions. The model can act on none of them: it cannot read `Rejected` or `Conflict`, and `UndeclaredRegionError` never reaches it at all, because it ends the loop it runs inside.


## Decision

`blackboard.tools` renders the four reads and two of the three writes of `AgentBoard` into what a model API accepts, and runs what the model asks for against a board. The descriptors are written rather than generated, and four rules hold the writing to the protocol.

**No schema asks for an identity.** The agent's name is carried by the `AgentBoard` the caller bound, and `AgentBoard` has no parameter for it. A model offered these tools writes under the name the caller chose and has no way to name another.

**The idempotency key is taken from the call.** A model API gives every call it asks for an identifier. That identifier fills `idempotency_key`, so a loop that sends one call twice writes once, and the second answer carries `repeated`. A caller that passes no identifier gets a write every time, which is what the parameter already means when it is left out.

**What the model can act on is answered, and what the caller must handle is raised.** ADR 0016 divides the outcomes of a write for a programmer, who catches exceptions and inspects values. A model does no catching and no inspecting, so `blackboard.tools` adds a division of its own. A refused write and a premise whose version moved come back as text, and so do the four conditions these calls raise: a region the board does not hold, a name of the other kind, a premise with no value yet, and a key that already wrote to another region. An exception from a store that cannot be reached is left to reach the caller, because no wording of it lets the model proceed.

A region named wrongly comes back naming the regions the board holds, so the correction is in the answer rather than in a further call the model would have to decide to make.

**A schema names exactly the parameters of the method it calls.** Every descriptor names the `AgentBoard` method it dispatches to and lists the parameters it withholds. A test asserts that the schema's properties together with the withheld names equal that method's parameters, so a written schema that falls out of step with the protocol fails that test.

### What is left out

Acknowledgment is not a tool. Acknowledging says the agent has stopped working on a notification, and the caller running the loop is what knows that; a model that acknowledged partway through its own reasoning would tell the run it had finished while it was still working.

`limit` is withheld from the two paged reads. `blackboard.tools` cuts a result that would not fit and says how much it left out, so the bound is applied where the size is known rather than guessed at by the model.

### What stays with the application

The application keeps the loop around the model, the prompt, and the provider's SDK. The library depends on no model API and calls none. A caller sends the offer, runs the calls this toolset owns, and hands the results back.

## Consequences

`blackboard.tools` is a seventh public import path, and costs nothing beyond the base install. It imports from the standard library and from this package, and from nothing else.

Two provider shapes are rendered from one set of descriptors, and the Model Context Protocol's shape is rendered alongside them, so a caller reaching a board through an MCP server offers the same tools under the same names.

A caller that runs `mypy --strict` can check a tool name without a type error, which is why the check is `ToolSet.owns` rather than `in`. `ToolSet` is a `Sequence[ToolDescriptor]`, and a container check against a string on such a sequence is an error mypy reports.

The descriptions are part of the interface rather than commentary on it, because the model acts on them at run time. They fall under the repository's writing standards for the same reason every other published sentence does, and a change to one changes behaviour.
