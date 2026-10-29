"""Errors raised by ic_python_db."""


class TooLargeError(ValueError):
    """An operation would touch more than ``constants.MAX_UNPAGED`` items in one call.

    On the IC each call has a fixed instruction budget, and running past it
    traps with no hint of the cause. This error is raised first instead, and
    its message names the paged alternative.
    """
