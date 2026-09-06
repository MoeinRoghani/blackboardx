# ADR 0027: A schema says which builds can read it

Date: 2026-09-05

## Status

Accepted.

## Context

A store stamped one number, the schema it wrote, and refused any database whose number was higher. The reasoning was that an older library must never read fields a newer one wrote and take them at face value.

That reasoning is right and the mechanism was too blunt. One monotonic integer cannot tell a database written by a newer build this one can still read from one it cannot, so it had to refuse both. Every schema change therefore stopped a rolling deploy: the two releases could not run against one database while the rollout proceeded, and that is the deployment this library exists for.

The cost was already recorded as a consequence to accept. It should not have been accepted.

## Decision

Two numbers, not one.

`SCHEMA_VERSION` is the schema this build writes. It rises on any change to the physical schema, additive or not.

`SCHEMA_COMPAT_VERSION` is the oldest `SCHEMA_VERSION` whose library still works against a database at `SCHEMA_VERSION`. A change that only adds a column or a table leaves it where it was, because a build that ignores them is unharmed. A change that gives something a new meaning raises it.

A store refuses a database only when the compatibility number it carries is higher than the version this build knows. A database that is merely newer is used, because the build that wrote it declared this one still able to.

A database carrying only the old number is refused when newer, exactly as before. It was written before the library made this promise, so it makes none.

## Consequences

Two releases run against one database whenever the schema change between them was additive, which is most of them. A rolling deploy stops only when the change genuinely cannot be shared, and then it stops for a stated reason rather than because the mechanism could not tell.

The refusal names both numbers, so an operator reads which build the database needs rather than only that this one will not do.

`SCHEMA_VERSION` rises to 4 to carry the second number, and `SCHEMA_COMPAT_VERSION` is 3. Schema 3 moved each agent's progress into the store, and a build older than that counts notifications in its own memory, so it cannot share a board with one that does not.

## Why not a boolean

`graphile-worker` marks each migration breaking or not, and refuses only when a breaking one was crossed. That answers the same question with less information: it says a change was incompatible without saying which builds survive it.

Synapse and Chromium both ship the two-number form instead, and Chromium states the rule in one line: check the version when upgrading, check the compatible version to see if you can use the file at all, and fail if that is larger than the code expects.

## Why refuse at all

Refusing on a bare integer is rare among task-queue libraries and normal among systems that own a store format. Temporal, Flyway, Django, Oban and River all decline it, and each is protecting a rolling deploy. Room, Gitea, Synapse, Chromium, Lucene, WiredTiger, Redis, Postgres and Prometheus all do it, and each owns a file or a store whose format it must not misread.

This library is the second kind and deploys like the first. The second number is what lets it be both.
