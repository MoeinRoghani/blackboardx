# Guides

The concepts say what the parts are. These say how to build with them.

Each guide answers one question and assumes the [concepts](../concepts/board.md)
behind it. Read them in the order below to build an application from nothing, or
go to the one that names your question.

## How to

| Guide | Answers |
| --- | --- |
| [Write an agent](writing-an-agent.md) | What an agent does when it is woken, what it declares, and what acknowledging commits it to |
| [Write an admission rule](admission-rules.md) | How the application refuses a write before the board sequences it |
| [End a run](ending-a-run.md) | What ends a run, who closes it, and how the outcome is read |
| [Serve a blackboard over HTTP](serving-a-blackboard.md) | Which path carries each operation, and what your service supplies |
| [Notify agents over HTTP](notifying-agents.md) | How a notification reaches an agent that is its own service |
| [Test an application](testing.md) | How to drive time, agents and a whole run without waiting |

The first three are the application's own code. The next two are what a
deployment adds. The last one applies throughout.

## Integrations

An agent decides its own work, and a language model is one way to decide. These
cover that case; nothing in the library depends on either.

| Guide | Answers |
| --- | --- |
| [Let a model decide](deciding-with-a-model.md) | How the board's operations become tools a model can call, against any provider |
| [Use the board inside LangChain](langchain.md) | How those tools join a loop LangChain already runs |

Read the first before the second. The second assumes the tool surface the first
describes.
