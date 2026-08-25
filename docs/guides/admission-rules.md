# Write an admission rule

The rule is where the application refuses a write: one that fails its own schema, duplicates something already on the board, or adds nothing.

## The signature

```python
from blackboard import Accept, Reject


def rule(proposed, reader):
    ...
    return Accept()  # or Reject("why")
```

It runs on **every** proposed write, of both kinds, before the board sequences anything. Supplying no rule accepts every write, subject to the region existing and the run being open.

## Telling the two kinds apart

```python
from blackboard import ProposedContribution, ProposedPremiseWrite


def rule(proposed, reader):
    if isinstance(proposed, ProposedContribution):
        return validate_bundle(proposed.content)
    if isinstance(proposed, ProposedPremiseWrite):
        return Accept() if proposed.premise != "window" else Reject("window is fixed")
    return Accept()
```

## Validating content

```python
def rule(proposed, reader):
    if not isinstance(proposed, ProposedContribution):
        return Accept()
    content = proposed.content
    if not isinstance(content, dict) or "findings" not in content:
        return Reject("a contribution carries findings")
    return Accept()
```

The reason reaches the writing agent, so write it for whoever has to fix the caller.

## Reading the board

The rule receives a read handle, so it can refuse a write for what the board already holds rather than for the write alone.

```python
def refuse_duplicates(proposed, reader):
    if isinstance(proposed, ProposedContribution):
        for existing in reader.read_level(proposed.level):
            if existing.content == proposed.content:
                return Reject("a duplicate of a contribution already on the board")
    return Accept()
```

## The check-then-act window

The rule runs without the control component's lock, so two writes judged at the same moment both see the board as it was before either landed. A duplicate-refusing rule therefore bounds concurrent duplicates rather than preventing them.

A premise write closes that window itself, because it names the version it expects to replace. A level write has no equivalent, so a rule that must be exact needs the uniqueness enforced where the contributions are stored.

## Where it does not apply

A premise's opening value bypasses admission. It reaches the board and takes a sequence number like any other write, but it is the application's own input rather than a proposal from a writer, so there is nothing for the rule to judge.
