LEVEL_MAX_DEFAULT = 3

# Hook action types
ACTION_CREATE = "create"
ACTION_MODIFY = "modify"
ACTION_DELETE = "delete"

# Largest collection an unpaged operation (iterating a relation, instances())
# loads in one call; past it they raise TooLargeError. At about 1M
# instructions per load this stays well inside a query's 5B budget.
MAX_UNPAGED = 1000
