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

#: The schema this version of the library reads and writes.
#:
#: Raise it when a change makes a record unreadable by an earlier version.
#: Adding a column an older version ignores is not such a change; removing
#: one, or giving one a new meaning, is.
SCHEMA_VERSION = 3


class SchemaVersionError(BlackboardError):
    """A record was written by a schema this version of the library cannot read.

    Upgrade `blackboardx` to a version that reads it. The library never
    rewrites a record backwards, because an older version would then read
    fields a newer one wrote and take them at face value.
    """


def stamp_to_write(found: int | None, *, where: str) -> int | None:
    """Returns the number to stamp on the record, or ``None`` to leave it.

    ``found`` is the number already on the record, or ``None`` where there is
    none. ``where`` names the database in the message a refusal carries.

    A record with no stamp is adopted rather than refused. Everything written
    before stamps existed is readable by this version, and refusing it would
    strand a record for a reason that is not true.
    """
    if found is None:
        return SCHEMA_VERSION
    if found > SCHEMA_VERSION:
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
