# Write an agent

An agent's work starts when the library notifies it and finishes when the agent reports that it has stopped.

1. It receives a notification.
2. It reads whatever it wants from the board.
3. It decides whether it has anything to add.
4. It writes each thing it wants to add.
5. It acknowledges.

The library asks nothing of an agent outside these steps. Steps 2 and 3 are invisible to it: reads go straight to the board, and nothing reports what the agent concluded. It learns what the agent wrote, because it sequenced those writes itself. Deciding to add nothing is an ordinary outcome and skips step 4.

## The smallest one

```python
from blackboard import Agent


def investigate(notification):
    window = model.reader.read_premise("window").value
    findings = look_for_trouble(window)  # your own work
    if findings:
        model.control.write("ocp", "platform", {"findings": findings})
    model.control.ack("ocp", notification.notification_id)


model.control.register_agent(Agent(name="ocp", notify=investigate))
```

## Declaring what wakes it

```python
Agent(
    name="ocp",
    notify=investigate,
    subscribes_to=["window", "namespace"],  # only these wake it
    writes_to=["platform"],  # it may write only here
)
```

Omit `subscribes_to` and the agent is woken by every premise and by no level. Name a level and a contribution to it wakes the agent, which is how one agent's finding starts another's work.

Omit `writes_to` and every level is permitted. Name one and a write to any other level comes back `Rejected` with the cause `NOT_PERMITTED`.

Naming a level that was never declared is a different failure, and it is caught earlier: `register_agent` raises `UndeclaredRegionError` rather than letting the agent register with a permission it can never use.

An agent is never woken by its own write.

## Joining a run

Name the agent when the run is created, which is how agents normally arrive.

```python
model = create_model(..., agents=[Agent(name="ocp", notify=investigate)])
```

An agent that joins a run already under way registers itself instead, and is woken the same way.

```python
model.control.register_agent(Agent(name="netops", notify=investigate))
```

Either way the agent is woken immediately, covering every subscribed region that already holds something, because an agent that has just joined is out of date with the whole board.

The callback therefore runs **before** `create_model` or `register_agent` returns. A callback that needs the model must be given it another way, because the call has not returned yet.

```python
holder = []


def investigate(notification):
    model = holder[0]
    ...


model = create_model(...)
holder.append(model)  # before registering
model.control.register_agent(Agent(name="ocp", notify=investigate))
```

## Doing the work elsewhere

The callback runs on the thread that dispatched. Nothing requires the agent to work there.

```python
def hand_off(notification):
    executor.submit(do_the_work, notification)  # return at once
```

Acknowledgment is everything the control component learns about how an agent ran. It records what the agent wrote, because it sequenced those writes itself, and it learns nothing about how long the work took, whether it succeeded, or where it happened. It never kills an agent, and an agent takes as long as it takes.

## What acknowledging means

It means the agent has stopped working on that notification. It does not mean the agent found anything, and it does not mean the agent will not be woken again.

When a run closes, it names every agent still holding a notification as unfinished.
