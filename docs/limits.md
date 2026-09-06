# What this version does not do

Every limit here is a limit of the library as it stands, checked against the
code rather than remembered. Where something is designed and not built, this
page says so and points at the design.

## A callback belongs to one process

The record is durable and so is the run. What is not is the callback.

| Word | Holds |
| --- | --- |
| **The record** | Regions, contributions, premise values and their versions, the sequence, idempotency keys, and the run's outcome with the agents that did not finish. Removed only by `store.delete`. |
| **The run** | The two deadlines, its agents with what wakes each and where it is reached, how far each has got, and what a write recorded that nothing has sent. Removed when the run closes. |
| **The callables** | `admission_rule`, `termination_predicate`, `clock`, `on_open`, `on_closed`, and the transport that reaches an address. Never written, so never removed. |
| **In flight** | Notifications `HttpNotifier` has queued but not yet sent. |

A write is served by any process. It lands on the record, pushes the idle
deadline, and records how far the agents that should hear of it have been
told. A process that never registered an agent still knows that agent is
owed an answer, and `close_expired` names it when the run closes.

Whether it can then reach the agent depends on how that agent was declared.
An `address` is data, so every process reads it and any process given a
transport as `reach` delivers to it. A `notify` callback is a Python object,
so the process that holds it is the only one that can call it.

An agent declared by callback alone is therefore woken from one process, and
that process finds the work by reading the record, through
`Control.notify_due`, which delivers what its own agents are owed and names
them. Call it on whatever schedule suits the deployment, beside
`close_expired`. A write taken in the same process notifies inline and needs
no poll, so a run inside one process never calls it.

The delay between a write on one replica and such an agent hearing of it is
therefore the poll interval plus whatever is left of the region's batch
window. Choose the interval and the idle limit together: a run whose idle
limit is shorter than the poll interval can settle before the poll notices.
An agent with an address on the run is woken by the replica taking the write
and pays neither.

Two replicas holding the same agent name both hold a callback, and both
deliver. A notification carries no values, so a repeat costs the wire
nothing, but it costs an application whose callback does real work. Give one
name one callback in one place, or an address and no callback.

## A notification is sent at least once, and may be sent twice

The intent to notify is a row written in the same transaction as the
contribution, so a process that commits a write and stops before delivering
has not lost it. `Control.relay` sends what is unsent, through a callback
this process holds or through the address the run records, and marks a row
only after the send returns.

Marking after sending rather than before is what makes delivery at least
once. A process that sends and stops before marking sends again, so a
notification may arrive twice. That costs nothing: a notification carries no
values, the agent reads the board either way, and cumulative acknowledgment
absorbs the extra identifier.

`HttpNotifier` still holds its own queue in memory, and `close` waits up to
`close_timeout` before abandoning what is left and reporting each one through
`on_failure`. Abandoning it loses the work only where the notifier was given
no store: a lane returns before the send, so the lane is what clears the row,
and a notifier with no store leaves that to the control component, which sees
the lane accept the notification and never learns what became of it.

## The library logs only what a caller cannot see

The rule is one line: an agent knows what it wrote, what it was refused, what
it was notified of and what it acknowledged, because each of those reached it,
so this library says none of them again. It logs a run closing with its
outcome and the agents that did not finish, and a notification that never
arrived after its attempts, because no agent can log what it never received.

It also logs what only the store layer sees: a record written for a schema
this version cannot read, a record stamped forward to this one, and a store
that failed under `close_expired`. A sweep has no caller waiting on it, so a
failure there reaches nobody unless the library says so. One board failing
does not end the pass; the sweep logs it and closes the rest.

Every line goes to the `blackboard` logger through the standard library, and
the application configures the handlers and the format.

## Every store call blocks

`BoardStore` has nineteen methods and not one is a coroutine, so every write,
read, acknowledgment and sweep waits for its database round trip. The record
and the run are both in the store, so those round trips are the ordinary path
rather than an occasional cost.

An application built on `asyncio` therefore has to keep them off its event
loop, because a blocking call inside `async def` stalls every other request on
that worker. FastAPI's own guidance is to declare a route that calls a
blocking library with plain `def`, which runs it in a threadpool; a route that
must stay `async def` puts the call on a thread with
`fastapi.concurrency.run_in_threadpool`, which is what
[Serve a blackboard over HTTP](guides/serving-a-blackboard.md) shows.

The agent half has both shapes already, `BoardClient` and `AsyncBoardClient`,
so an agent may be written either way. The blackboard half has one.

## There is no authentication and no authorisation

`BoardService` authenticates nobody. It does not check that the `writer` named
in a body is the caller who sent it, and it has no notion of which agents may
write where beyond the `writes_to` an application declared.

Each operation has its own path and method so that a gateway in front of the
service can carry those policies, which is the deployment this library is
built for. Check the caller in your route, before calling into the service.

## Content must be JSON

Content crosses every store as JSON, including the in-memory one, so what is
written comes back as what JSON carries: a tuple comes back as a list, and
content that JSON cannot carry raises `TypeError` before anything is stored.

That is deliberate rather than pending. A store that held Python objects as
they stand would accept in a test what a deployment then refuses.

## An idempotency key is not compared against the write it names

A key already written returns the first write's outcome whatever content
arrives with it. Only the region is compared, and a key that already wrote one
region and is then sent naming another raises `IdempotencyKeyError`, which
over HTTP is a 409 whose body reads `{"error": "idempotency_key_reused"}`.

Comparing bodies means normalising what JSONB and BSON did to them on the way
in, which is where a false mismatch would come from. A retry is expected to
send what it sent before.

## A read over HTTP stops at a thousand

A read in process takes `limit=None`: `model.reader.read_level`
returns every contribution from the sequence bound. A read over HTTP is
bounded twice. It answers with `wire.DEFAULT_LIMIT`, a hundred, when the
caller names no limit, and never with more than `wire.MAX_LIMIT`, a thousand,
whatever the caller asks for.

The cap is silent. A request for five thousand is answered with a thousand and
no error, and `has_more` is the only thing that says the level continues. A
caller that reads a whole level follows `has_more` and moves `from_sequence`
past the last sequence it saw. `BoardClient.read_level` and
`BoardClient.read_board` do that themselves when they are given no `limit`;
given a `limit`, they make a single request and return at most that many
contributions.

## A batch window is not on the record

A store holds a region's name and its kind. It holds no batch window, so
`read_regions` returns every region with the default window of zero, whatever
window was declared, and `wire.RegionBody.declaration` rebuilds it the same way
on the other side.

The window is neither the record nor the run, so every process serving a board
states its windows again in the `regions` it passes. Names and kinds are
checked against the record and nothing else, so a window that disagrees with
another process's is not reported.

## Two versions run side by side only when the schema says so

A store records two numbers: the schema it wrote, and the oldest library that
can still read what it wrote. A database that is merely newer is used; one
whose second number is above what this build knows is refused.

That is what decides whether a rolling deploy works. A release that only adds
a column or a table leaves the second number where it was, so the old and new
builds run against one database while the rollout proceeds. A release that
gives something a new meaning raises it, and then the two cannot run together
and the deploy has to stop everything.

One number could not tell those apart, so it had to refuse every newer
database and made every schema change a full stop.

## A record is never stamped backwards

A store refuses a record written for a schema it cannot read, and never
rewrites one to an earlier schema. If it did, an older version of the library
would read fields a newer one wrote and take them at face value.

Upgrade forwards. There is no supported path back.

## The blackboard reaches out; the agent does not

An agent is notified by a request the blackboard makes to an address the agent
gave it, so an agent has to be reachable at an address. There is no long poll,
no stream, and no queue an agent subscribes to. An agent behind a network that
will not accept an inbound request cannot be notified by this library.

## Closing a run clears what it needed

The outcome and the agents that did not finish are stamped into the run as it
closes, so everything that write consulted goes with it: how far each agent
had been told and had answered, and any notification a write recorded that
nothing has sent. A closed board holds its regions, its contributions, its
premise values, and one row saying how the run ended.

An acknowledgment arriving after that finds nothing owed. The run has ended,
so it changes nothing and reports nothing, which is what it would have changed
had the row still been there.

## Deleting is the application's to schedule

`store.delete` removes one board. Nothing in the library calls it: a run that
closes deletes no board, and no board expires. There is no retention policy,
no sweep, and no age after which anything is deleted.

`store.delete` also cannot see a live run. Close the run before deleting the
board it is
serving.

## A tool schema does not name the board's regions

`blackboard.tools` renders one set of schemas, and they are the same whatever
board they are used against. `blackboard_write` takes a level as a string, and
nothing in that schema says which levels the board holds, so a model learns
them by calling `blackboard_read_regions` or by being told in the prompt it was
given.

A model that names a region the board does not hold is answered with the ones
it does, so the correction costs a turn rather than ending the run.

## The library calls no model API

`blackboard.tools` renders schemas and runs the calls a model asked for. It
sends nothing to a model, holds no conversation, and depends on no provider's
package. The loop, the prompt, and the choice of model are the application's,
and [Give an agent a language model](guides/deciding-with-a-model.md) shows the loop that
runs around this module.

## The conformance suite defines behaviour, not performance

`blackboard.conformance` decides a store's correctness. It says nothing about
how fast a store is, how it behaves under load, or how many boards it will
hold. Those are yours to measure against your database.

## The sweep closes runs and does not relay

`Sweep` runs `close_expired` on an interval, and nothing else. The relay and
`notify_due` are methods on a `Control` rather than functions over a store,
because sending needs the callables and a store holds none, so an application
that wants either on a schedule writes that loop itself.

## What is designed and not built

The convenience loop covers one of the two jobs that need a schedule. Every
other part of the design is built: the run's deadlines and its outcome, the
agents of a run and how far each has been told and has answered, the sweep
that closes what nobody is watching, and the outbox that keeps a notification
a process was holding when it stopped.


