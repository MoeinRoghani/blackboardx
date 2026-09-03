# ADR 0022: The application composes the tool list

Date: 2026-09-03

## Status

Accepted. Supersedes ADR 0021 on who groups the tools and on withholding acknowledgment. Every other decision in ADR 0021 stands.

## Context

ADR 0021 settled how a method of `AgentBoard` becomes a tool, and those rules hold. It also made two groupings that the rules did not require.

`blackboard.tools` exported one bundle, `TOOLS`, holding every tool, with `select` and `read_only` to narrow it. The library's grouping was therefore the starting point and an application's choice was a subtraction from it. Which tools a model should be able to reach for is a decision about one agent on one board, and the application is what knows it.

Acknowledgment was withheld outright. The reason given was that acknowledging says the agent has stopped, and a model that sends it partway through its own reasoning tells the run this agent has finished while it is still working. That risk is real. It is a reason for an application to leave the tool out, and it was used instead as a reason the library would never offer it.

## Decision

Each tool is exported under its own name: `READ_REGIONS`, `READ_LEVEL`, `READ_PREMISE`, `READ_BOARD`, `WRITE`, `SET_PREMISE` and `ACK`. `ALL` is a plain tuple of the seven, for an application that wants every one.

`ToolSet` holds the descriptors an application chose. It renders them into a provider's shape and runs the calls it owns, and it stays one object because those two halves must agree: an application cannot offer one list and dispatch against another, and a name it does not hold raises rather than reaching a board.

A single descriptor renders on its own, through `for_anthropic`, `for_openai` and `definition`, for an application that assembles the provider payload itself.

`TOOLS` is removed, and so are the module-level `for_anthropic`, `for_openai`, `definitions` and `run`. Each was a shortcut for the bundle.

`ACK` is offered like every other tool. What ADR 0021 said about a model acknowledging early is still true, so the guides say it where an application decides, and an application that keeps acknowledgment in the loop around the model leaves `ACK` out of the set it builds.

## Consequences

`UnknownNotificationError` returns to the conditions answered rather than raised, because a call can now raise it. The answered list is five, and it is again exactly what the exported tools can raise.

Two agents on one board can be offered different tools from one import, which is what an application running a reviewer beside a contributor needs.

A smaller set is not a permission boundary. What an agent may write is settled by its `writes_to` declaration and by the admission rule, which the control component applies wherever a write came from, and a set shapes what a model reaches for rather than what the run allows.

Nothing here was released. `blackboard.tools` reaches PyPI for the first time in the release that carries this decision, so no name is deprecated and no caller is broken.
