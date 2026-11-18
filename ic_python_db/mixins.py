"""Mixins for adding functionality to database entities."""

from typing import Any, Dict, Optional

from .system_time import SystemTime


class TimestampedMixin:
    """Mixin that adds creation and update timestamps to entities."""

    def __init__(self):
        self._timestamp_created = 0
        self._timestamp_updated = 0
        self._creator = None
        self._updater = None
        self._owner = None

    def _update_timestamps(self, caller_id: Optional[str] = None) -> None:
        """Update timestamps and track who made the changes.

        Args:
            caller_id: ID of the user/process making the change
        """
        now = SystemTime.get_instance().get_time()
        if self._creator is None:
            self._timestamp_created = now
            self._creator = caller_id
            self._owner = caller_id
            # A row saved by a migration, before it was claimed, stores "None"
            self.__dict__.pop("timestamp_created", None)

        self._timestamp_updated = now
        self._updater = caller_id
        # A loaded entity still holds the timestamp_updated it was stored with
        self.__dict__.pop("timestamp_updated", None)

    def _restore_state(self) -> None:
        """Set the owner, creator and updater back from what a load stored.

        serialize() writes the state as ``owner``, ``creator`` and so on, and a
        load assigns each stored field by name, so they arrive as plain
        attributes while the mixin keeps its state in ``_owner``, ``_creator``
        and so on. Without this, a loaded entity has no owner, so nothing is
        checked when it is saved, and the stored ``updater`` overwrites the one
        a save sets.

        The two timestamps stay as the text they were stored as: it is written
        back unchanged by a save that does not set its own. ``_update_timestamps``
        drops ``timestamp_updated`` when it sets a new one, and
        ``timestamp_created`` when it claims a row that had no creator. The text
        is not read as a time, which a canister's ``datetime`` could not write
        back.

        A field the class declares under one of these names is a Property,
        which keeps its value elsewhere: it is not touched, and the mixin
        cannot restore that part of its state.
        """
        self._owner = self.__dict__.pop("owner", None)
        self._creator = self.__dict__.pop("creator", None)
        self._updater = self.__dict__.pop("updater", None)

    def set_owner(self, new_owner: str) -> None:
        """Set a new owner for this entity.

        Args:
            new_owner: ID of the new owner
        """
        self._owner = new_owner

    def check_ownership(self, caller_id: str) -> bool:
        """Check if the caller owns this entity.

        Args:
            caller_id: ID of the caller to check

        Returns:
            bool: True if caller owns the entity, False otherwise
        """
        return self._owner == caller_id

    def serialize(self) -> Dict[str, Any]:
        """Convert timestamps and ownership info to a dictionary.

        Returns:
            Dict containing the timestamp and ownership data
        """

        return {
            "timestamp_created": SystemTime.format_timestamp(self._timestamp_created),
            "timestamp_updated": SystemTime.format_timestamp(self._timestamp_updated),
            "creator": self._creator,
            "updater": self._updater,
            "owner": self._owner,
        }
