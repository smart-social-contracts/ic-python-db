"""Errors raised by ic_python_db."""


class TooLargeError(ValueError):
    """An operation would touch more than ``constants.MAX_UNPAGED`` items in one call.

    On the IC each call has a fixed instruction budget, and running past it
    traps with no hint of the cause. This error is raised first instead, and
    its message names the paged alternative.
    """


class MigrationError(Exception):
    """A row's ``migrate()`` raised while the row was being loaded.

    The message names the row (``Type@id``) and the versions; the error that
    ``migrate()`` raised is the ``__cause__``. It is deliberately not a
    ``ValueError`` or ``AttributeError``: listings skip a row that raises one
    of those because of a broken relation, and must not skip this one.
    """


class StaleEntityError(Exception):
    """A write through an entity read in an earlier message, whose row another
    message has changed (or deleted) since.

    A save writes every field the instance holds, so it would put back the
    values the other message wrote. Load the entity again and redo the change.
    """
