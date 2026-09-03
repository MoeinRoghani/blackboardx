# API reference

The public surface is seven import paths: the package and six submodules.
Everything else in the package is internal, and a name outside these pages may
change without a deprecation.

| Import path | Holds | Imported by |
| --- | --- | --- |
| `blackboard` | The board, the control component, `create_model` and `attach_model`, and the `AgentBoard` an agent body is written against | The blackboard and the agent |
| `blackboard.wire` | The request and response bodies that cross between the two halves, and the seven operations that carry them | Both |
| `blackboard.server` | Answering an agent's request, without a web framework | The blackboard |
| `blackboard.delivery` | Sending notifications to agents over HTTP | The blackboard |
| `blackboard.agent` | Reading and writing a board from an agent | The agent |
| `blackboard.tools` | Each method of `AgentBoard` as a tool a language model can call, for the application to choose among | The agent |
| `blackboard.conformance` | The suite every store implementation is held to | Anyone writing a store |

## `blackboard`

::: blackboard

## `blackboard.wire`

::: blackboard.wire

## `blackboard.server`

::: blackboard.server

## `blackboard.delivery`

::: blackboard.delivery

## `blackboard.agent`

::: blackboard.agent

## `blackboard.tools`

::: blackboard.tools

## `blackboard.conformance`

::: blackboard.conformance
    options:
      members: false
