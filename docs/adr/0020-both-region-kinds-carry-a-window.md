# ADR 0020: Both region kinds carry a batch window

Date: 2026-09-02

## Status

Accepted. Supersedes the specification's rule that only a premise carries a batch window, and completes ADR 0008.

## Context

The batch window is the only damping this library has. A premise declared with a five second window collects the changes that land inside it and issues one notification.

A level had none. Ten writes to a level a subscriber watches produced ten notifications, and a notification carries no values, so all ten said the same thing: read the board. The specification prices each notification at one inference where the agent is a language model, so a burst of ten findings cost ten runs of a model to learn what one notification would have said.

The rule came from the specification, written when only premises had subscribers. ADR 0008 gave levels subscriptions and left the rule in place, so the asymmetry was a leftover rather than a decision.

## Decision

`Level` takes a `batch_window`, defaulting to zero, with the same non-negative check `Premise` has. The control component records a window for both kinds, so the lookup in `_note_region_change` is direct rather than a lookup with a zero fallback, and a region nobody declared raises there rather than silently taking a zero window.

Registering an agent makes a level it subscribes to due at once, whatever that level's window, because registration is a catch-up on what is already on the board rather than a burst to damp. A premise it subscribes to is due after that premise's window, which is unchanged and is why an agent subscribed to premises alone waits the shortest of those windows for its first notification.

## Consequences

An application that batches its findings sets one argument. The default is zero, which dispatches inline, so nothing changes for anyone who does not.

`Level("f") == Level("f")` still holds and `Level` is still hashable, because the field is defaulted on a frozen dataclass.

`RegionBody.declaration()` rebuilds a `Level` without its window, as it already did for `Premise`. A store records a region's name and kind and nothing else, because the window tells the control component when to notify and is no part of the record. `docs/concepts/storage.md` already says this of premises and now says it of both.

A window longer than the run's idle limit delays a notification past the point where the run could settle. That was already true of premises and is documented rather than prevented, because the two limits answer to different things and the library does not know which the application meant.
