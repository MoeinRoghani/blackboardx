# API reference

The public surface is six modules. Everything else in the package is
internal, and a name outside these pages may change without a deprecation.

| Module | Holds | Imported by |
| --- | --- | --- |
| `blackboard` | The board, the control component, and model creation | The blackboard |
| `blackboard.wire` | The request and response bodies that cross between the two halves | Both |
| `blackboard.server` | Answering an agent's request, without a web framework | The blackboard |
| `blackboard.delivery` | Sending notifications to agents over HTTP | The blackboard |
| `blackboard.agent` | Reading and writing a board from an agent | The agent |
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

## `blackboard.conformance`

::: blackboard.conformance
    options:
      members: false
