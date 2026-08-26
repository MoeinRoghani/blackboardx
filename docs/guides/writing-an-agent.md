# Write an agent

An agent's cycle has five steps and no others.

1. It receives a notification.
2. It reads whatever it wants from the board.
3. It decides whether it has anything to add.
4. It writes each thing it wants to add.
5. It acknowledges.

Deciding to add nothing is an ordinary outcome and skips step 4.

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

Omit `writes_to` and every level is permitted. Name one and a write anywhere else is refused with `NOT_PERMITTED`, at registration time if the level does not exist.

An agent is never woken by its own write.

## Registering wakes it

`register_agent` delivers a notification immediately, covering every subscribed region that already holds something. A newly registered agent is out of date with the whole board, and the notification says so.

This means the callback runs **during** `register_agent`. A callback that needs the model must therefore be given it another way, because the call has not returned yet.

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

The control component learns nothing about an agent except that it acknowledged. It never kills an agent, and an agent takes as long as it takes.

## What acknowledging means

It means the agent has stopped working on that notification. It does not mean the agent found anything, and it does not mean the agent will not be woken again.

When a run closes, it names every agent still holding a notification as unfinished.
