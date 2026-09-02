# Serve a blackboard over HTTP

Agents that run as their own services read and write over HTTP. The
operations they need are the ones `Control` already has, so the library
states which path carries each one and answers them; your service keeps its
own HTTP server, its framework, and its authentication.

`BoardService.handle` takes a method, a path, and a decoded body, and returns
a status, headers, and a body. It imports no web framework and opens no
socket.

## Mounting it

One `Control` serves one board, so the service holds the runs it is
responsible for and `control_for` finds the one a request names.

Give it the store as well and a read is answered from the record when no run
is held, so any replica answers a read for any board the store holds:

```python
service = BoardService(control_for=runs.get, store=store, prefix="/v1")
```

Writes still need the run, and answer 404 without one. A board the store never
held answers 404 either way.

`on_open` and `on_closed` on `create_model` and `attach_model` are how `runs`
fills and empties:

```python
def route(model):
    runs[model.board_id] = model.control


def forget(outcome):
    runs.pop(board_id, None)


model = create_model(..., on_open=route, on_closed=forget)
```

`on_open` runs before the first agent is woken, which matters because waking
an agent runs its callback on this thread: without it, an agent that reads
back through this service meets 404 for the board that is creating it.

=== "FastAPI"

    ```python
    from fastapi import FastAPI, Request as Incoming
    from fastapi.responses import JSONResponse, Response as Bare

    from blackboard import Control
    from blackboard.server import BoardService, Request

    runs: dict[str, Control] = {}
    service = BoardService(control_for=runs.get, prefix="/v1")
    app = FastAPI()


    @app.api_route("/v1/{rest:path}", methods=["GET", "POST", "PUT"])
    async def blackboard(rest: str, incoming: Incoming):
        answer = service.handle(
            Request(
                method=incoming.method,
                path=incoming.url.path,
                body=await _json(incoming),
                query=dict(incoming.query_params),
            )
        )
        if answer.body is None:
            return Bare(status_code=answer.status, headers=dict(answer.headers))
        return JSONResponse(
            answer.body, status_code=answer.status, headers=dict(answer.headers)
        )


    async def _json(incoming: Incoming) -> object:
        if not await incoming.body():
            return None
        return await incoming.json()
    ```

=== "Flask"

    ```python
    from flask import Flask, jsonify, request

    from blackboard import Control
    from blackboard.server import BoardService, Request

    runs: dict[str, Control] = {}
    service = BoardService(control_for=runs.get, prefix="/v1")
    app = Flask(__name__)


    @app.route("/v1/<path:rest>", methods=["GET", "POST", "PUT"])
    def blackboard(rest: str):
        answer = service.handle(
            Request(
                method=request.method,
                path=request.path,
                body=request.get_json(silent=True),
                query=request.args.to_dict(),
            )
        )
        body = jsonify(answer.body) if answer.body is not None else ""
        return body, answer.status, dict(answer.headers)
    ```

`handle` raises nothing you have to catch. A board this service does not
hold, a region nobody declared, a body that will not decode, and a run that
has closed all come back as a status and a body.

## The operations

| Method | Path | Answers |
| --- | --- | --- |
| `GET` | `/boards/{board_id}/regions` | Every declared region and its kind |
| `GET` | `/boards/{board_id}/levels/{level}` | A page of that level |
| `GET` | `/boards/{board_id}/premises/{premise}` | Its current value and version |
| `GET` | `/boards/{board_id}/changes` | A page of every write, in order |
| `POST` | `/boards/{board_id}/levels/{level}` | The sequence the write took |
| `PUT` | `/boards/{board_id}/premises/{premise}` | The version the set produced |
| `POST` | `/boards/{board_id}/acknowledgements` | Nothing |

The paths above are `blackboard.wire.OPERATIONS`, and both halves build them
from those objects rather than from a string of their own. Mount the prefix
you gave `BoardService`; the rest of each path is the library's.

The two reads that return a page take `limit` and `from_sequence` as query
parameters. A read that names no `limit` answers with `blackboard.wire.DEFAULT_LIMIT`
rows, and a `limit` above `MAX_LIMIT` is capped at it, silently. A page says
`has_more` when it stopped early, and the reader continues from one past the
last sequence it received. A sequence number is the cursor because an offset
shifts when a concurrent write lands.

A read in process takes `limit=None` and means unbounded, because it is not
paying for a page. A read over HTTP is a page whether or not the caller chose
a size.

## What a request body carries

| Operation | Body | Required |
| --- | --- | --- |
| `POST .../levels/{level}` | `WriteRequest` | `writer`, `content` |
| `PUT .../premises/{premise}` | `SetPremiseRequest` | `writer`, `value`, `expected_version` |
| `POST .../acknowledgements` | `AckRequest` | `agent`, `notification_id` |

`content` and `value` are required because a body that carries neither would
otherwise store `null`, and setting a premise to `null` wakes every
subscriber to it.

`WriteRequest.level` and `SetPremiseRequest.premise` are optional and are
ignored where the path names the region, which it always does on these
routes. They exist so that a service mounting a single route of its own still
receives a body that says what it is.

## What the status codes mean

| Status | Meaning | Send it again? |
| --- | --- | --- |
| 200 | A read, or a write this key had already made | |
| 201 | A write reached the board for the first time | |
| 204 | An acknowledgment was recorded | |
| 400 | The body or a query parameter could not be read | No |
| 404 | No such board, region, notification, or path | No |
| 405 | That path takes a different method | No |
| 409 | A premise moved on, or an idempotency key named another region | Read the premise and decide again, or fix the key |
| 410 | The run has closed and takes no more writes | No |
| 422 | The write was refused; the body names the cause | No |

Every one of those is an answer rather than a fault, so a client that sends
the same request again gets the same answer. Only a 5xx and a failure to
connect are worth another attempt.

Three of those carry a body of their own rather than an `ErrorBody`: 409 is a
`ConflictBody` naming the premise's current version, 422 is a `RejectedBody`
naming the cause, and 201 and 200 are a `WrittenBody`. The rest, 400, 404 and
405, are an `ErrorBody`, where `error` is a stable name to branch on and
`detail` is written for a person reading a log.

## Writing once

A write body may carry an `idempotency_key`. The blackboard writes one key
once: a key it has already written answers 200 with the first write's
sequence and `repeated`, and adds nothing, where a first write answers 201. A
key sent for a region it did not name before is refused 422 with the cause
`idempotency_key_reused`.

Nothing in the service does that work. The key goes to the control component
and from there to the store, where the row and its key are written together.
[Storage](../concepts/storage.md#writing-once-over-a-network-that-may-repeat)
covers what a store guarantees.

## The path names the region

A write is addressed to `/boards/{id}/levels/{level}`, and that is the level
it lands in. A body naming a different one changes nothing, so a caller
cannot write somewhere it did not address, and a gateway policy written
against the path holds.

## Authentication

The library has none. The service is behind your gateway, the gateway
authenticates the caller, and each operation has a path and a method for a
policy to be written against. A read-only agent is given the four `GET` paths
and nothing else.

`handle` does not check that the `writer` in a body is the caller. Do that in
the route, before calling in, from whatever the gateway put on the request.

## Board identifiers

`handle` matches the path a segment at a time and decodes each variable once,
so an identifier holding a slash arrives whole as long as your framework
hands over the path it received. Some frameworks decode the path first. A
UUID travels through all of them unchanged, and is the identifier to use.

## Notifying the other way

This page covers what an agent sends. [Notify agents over
HTTP](notifying-agents.md) covers what the blackboard sends, which needs the
`notifier` extra. `blackboard.server` needs none: it is in the base install
and depends on nothing.
