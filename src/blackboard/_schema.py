"""What wrote a record, and the check that this version can read it.

A store opens any database it is pointed at, creating what is missing and
reading what is there. Nothing checked that what is there was written by a version this
library understands.

That has been survivable because every schema change so far added something.
It stops being survivable the first time one does not, and the failure is the
worst kind: a run starts, an agent writes, and a read comes back missing a
column, so the error names a query rather than the cause.

A store therefore records a schema number and checks it when the store opens. The
number counts changes to the physical schema, not releases: most releases
change no schema, and a check that fires on every release is a check nobody
can leave on.
"""

from __future__ import annotations

import logging

from blackboard._board import BlackboardError

logger = logging.getLogger("blackboard")

#: The schema this version of the library writes.
#:
#: Raise it on any change to the physical schema, additive or not.
SCHEMA_VERSION = 4

#: The oldest ``SCHEMA_VERSION`` whose library still works against a database
#: at :data:`SCHEMA_VERSION`.
#:
#: One number says what this build writes; this one says how far back a build
#: may be and still read it. A store refuses a database whose compatibility
#: number is higher than the version this build knows, and accepts one that is
#: merely newer.
#:
#: That distinction is what lets two releases run against one database while a
#: deployment rolls. A change that only adds a column or a table leaves this
#: where it is, because a build that ignores them is unharmed. A change that
#: gives something a new meaning raises it to the new ``SCHEMA_VERSION``.
#:
#: It is 3 because schema 3 moved each agent's progress into the store: a
#: build older than that counts notifications in its own memory, so it cannot
#: share a board with one that does not.
SCHEMA_COMPAT_VERSION = 3


class SchemaVersionError(BlackboardError):
    """A record was written by a schema this version of the library cannot read.

    Upgrade `blackboardx` to a version that reads it. The library never
    rewrites a record backwards, because an older version would then read
    fields a newer one wrote and take them at face value.
    """


def stamp_to_write(
    found: int | None, *, where: str, compat: int | None = None
) -> int | None:
    """Returns the number to stamp on the record, or ``None`` to leave it.

    ``found`` is the number already on the record and ``compat`` the
    compatibility number beside it, each ``None`` where there is none.
    ``where`` names the database in the message a refusal carries.

    A database newer than this build is refused only when it says so, through
    a compatibility number higher than what this build knows. A database that
    is merely newer is used, because the build that wrote it declared this one
    still able to.

    A record with no stamp is adopted rather than refused. Everything written
    before stamps existed is readable by this version, and refusing it would
    strand a record for a reason that is not true.
    """
    if compat is not None and compat > SCHEMA_VERSION:
        logger.error(
            "%s holds a record needing schema %d at the oldest,"
            " and this version writes %d",
            where,
            compat,
            SCHEMA_VERSION,
        )
        raise SchemaVersionError(
            f"{where} holds a record written for schema {found},"
            f" which requires blackboardx at schema {compat} or newer,"
            f" and this version is at {SCHEMA_VERSION}."
            " Upgrade blackboardx to a version that reads it."
        )
    if found is None:
        return SCHEMA_VERSION
    if found > SCHEMA_VERSION and compat is None:
        # The caller of an ordinary operation sees this raised. A scheduled
        # sweep has no caller, and a store opened at start-up may raise into
        # a place nobody is reading, so it is said here as well.
        logger.error(
            "%s holds a record written for schema %d, and this version reads %d",
            where,
            found,
            SCHEMA_VERSION,
        )
        raise SchemaVersionError(
            f"{where} holds a record written for schema {found},"
            f" and this version of blackboardx reads {SCHEMA_VERSION}."
            " Upgrade blackboardx to a version that reads it."
        )
    if found < SCHEMA_VERSION:
        logger.info(
            "%s holds a record written for schema %d, stamping it %d",
            where,
            found,
            SCHEMA_VERSION,
        )
        return SCHEMA_VERSION
    return None
