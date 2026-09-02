# Write an admission rule

The rule is where the application refuses a write: one that fails its own schema, duplicates something already on the board, or adds nothing.

## The signature

```python
from blackboard import Accept, Reject


def rule(proposed, reader):
    ...
    return Accept()  # or Reject("why")
```

It runs on every proposed write the control component puts to it, of both kinds, before the board sequences anything. Supplying no rule accepts every one of them. [Where it does not apply](#where-it-does-not-apply) names the writes it is never asked about.

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

`ProposedContribution` carries `writer`, `level` and `content`.
`ProposedPremiseWrite` carries `writer`, `premise`, `value` and
`expected_version`. Both name the writer, so a rule can refuse a write for
who made it as well as for what it carries.

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

A premise write closes that window itself, because it names the version it expects to replace. A level write has no equivalent, so a rule that has to be exact rather than approximate needs a unique constraint in the database the board writes to.

## Where it does not apply

Some writes are settled before the rule is asked, so no rule sees them.

A premise's opening value bypasses admission. It reaches the board and takes a sequence number like any other write, but it is the application's own input rather than a proposal from a writer, so there is nothing for the rule to judge.

A write naming a region nobody declared raises `UndeclaredRegionError`, and one naming a region declared as the other kind raises `RegionKindError`. Both raise before the rule runs, so a rule never has to check that the region it is judging exists.

A write to a closed run comes back `Rejected` with the cause `RUN_CLOSED`, and a registered agent writing to a level its `writes_to` does not name comes back with the cause `NOT_PERMITTED`. Both are decided before the rule runs, and `ADMISSION` is the only cause a rule produces.

## The names

`AdmissionRule` is the type of the callable `create_model` takes: it receives a
`ProposedWrite` and a `BoardReader`, and answers `Accept` or `Reject`.

`ProposedWrite` is `ProposedContribution | ProposedPremiseWrite`, so a rule that
cares which kind it was given branches on `isinstance`. A contribution names a
`level` and a premise write names a `premise` and the `expected_version` it
means to replace, and both name the `writer`.

A rule never sees a write to a region nobody declared. That raises
`UndeclaredRegionError` before the rule is reached, because the application
declared the regions and a name outside them is its own mistake rather than
something for the rule to judge.
