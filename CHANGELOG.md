# Changelog

## Unreleased

### Fixed

- `TimestampedMixin` no longer loses its owner when a row is loaded. The mixin keeps its state in `_owner`, `_creator` and `_updater`, and stores it under `owner`, `creator` and `updater`. A load assigned the stored names as plain attributes and left the underscore ones at their initial values, so an entity loaded from storage (in a canister, every entity in a message after the one that created it) had no owner and `Entity._save` checked nothing: a caller other than the owner could write it, and the row did not record who had, because the stored `timestamp_updated` and `updater` overwrote the new ones on each save. The state is now restored on load, the check holds, and a save writes its own `updater` and `timestamp_updated`. The stored `timestamp_created` is written back as it was stored, as before; the stored text is never read as a time, so it does not matter that a canister's `datetime` cannot write one.
- The save that persists a migration when a row is loaded is not a write by the caller: it does not check the owner, and it leaves `updater` and `timestamp_updated` as stored. (With the check restored on load, a reader who did not own a row that needed migrating would have been refused.)
- A row stored before its class had the mixin, or by an earlier release of this library, loads as before. A row with an owner keeps it; a row without one is claimed by the first caller to save it, as it was (a read that migrates it does not claim it).
- A row whose `migrate()` raises no longer disappears from listings. `load_some` and `page` skipped a row that raised `ValueError` or `AttributeError` while it was built, which was meant for rows with a broken relation but also caught the error of `migrate()`. A bug in a `migrate()` left the rows counted by `count()` and missing from `load_some`, `page`, `instances` and `find`, with a warning in the log as the only trace, while `load()` of the same row raised.

### Changed

- **A listing that returned a partial result now raises.** `load_some`, `page`, `instances`, `find` and the index rebuilds raise `MigrationError` when the `migrate()` of a stored row raises, the way `load()` does. A row that cannot be built because of a relation is still skipped with a warning: one that carries a value for a `OneToMany`, or whose `ManyToMany` list names a row that is gone.
- **`load()` raises `MigrationError` for such a row, no longer the error `migrate()` raised.** The message names the row (`Cfg@1`) and the versions it was migrated between, and the original error is its `__cause__`. Code that caught that error from `load()`, or from anything else that loads a row (`Entity[id]`, `find_by`, `top_by`, a relation's getter, iterating or paging a `RelationView`, the lookup `deserialize()` does to find the row to update), must catch `MigrationError`.

### Added

- `MigrationError`, in `ic_python_db` and `ic_python_db.errors`.

## 0.15.0 (not released)

### Fixed

- A field-index value that ends like an index key (`V1|n0000000000`) no longer hides other values' records. Its count key sat among the entries of `V1`, the page read of `V1` came back short, and `find_by` and `RelationView.page`, which take a short page for the last one, stopped: every id after it was unreachable. Any writer of a value could do it.
- A range read of a posting list goes on past keys that are not entries of its list (it already did in `_ix_all` and the purge; `_ix_page` stopped). It restarts at the last key it read instead of at the next char, which is not the next key for U+007F, U+07FF and U+FFFF, has no successor for U+10FFFF, and is a surrogate that cannot be encoded for U+D7FF.
- The entry of a custom id that reads as a JSON list is no longer taken for a list stored before 0.13 and removed.
- A store written before 0.13 that holds the lists of both a value `V` and `V|c`, `V|d`, `V|n` and 12 digits, or `V|h` and 24 hex digits no longer makes every read and write of `V` fail. The bare key of such a value is where later releases keep V's count (`|c`), mark (`|d`) or an entry (`|n…`, `|h…`), so reading V's count raised `ValueError` on the other value's array, an entry key listed that array's text as an id, and a write to V could replace it. A key of V's own that holds another value's old list now has that list converted first (which moves it out of the way), an entry counts only if it holds the id its key spells, and the repair no longer stalls on it.

### Changed

- **Field-index keys.** A value with a `|` in it is filed under `_fx:<Type>:<field>:<escaped value>` (`|` as `%7C`, `%` as `%25`), not under `_fi`. Every other value keeps its key, byte for byte: nothing moves for them, a value with only a `%` in it included.
- While entries of such a value may be under the old key, each `field_index_*` call also reads it, `field_index_add` and `field_index_remove` take the id off it, and a merged `field_index_get` / `field_index_page` lists an id once.

### Added

- `Database.repair_field_indexes(after="", limit=500)`: walks the field-index keys (of any length), in bounded calls. For values with a `|` it moves old entries to their present key, removes those whose entity is gone or has another value, and converts a list a release before 0.13 stored as one array.
- `Entity.rebuild_field_indexes(from_id, batch)`: `rebuild_field_index` for every indexed field of a type at once. `rebuild_field_index` moves old entries too.

### Upgrading from 0.14

1. Upgrade. Nothing is wrong meanwhile: values without a `|` are untouched, and those with one are read under both keys.
2. Run `Database.repair_field_indexes` until it returns `None`, in calls of `limit` keys. This moves every old entry, custom ids included, and converts old-form lists of values with a `|`; nothing needs to run before it (`rebuild_field_indexes` is optional). Correctness does not wait for it; it is what lets a later release drop the old-key reads, once it has run to its end. It leaves what it cannot judge: entries of a type or field the code does not index; entries whose row has no such field; an old-form list of more than 100 thousand ids (logged; a read of its value converts it where it fits the message, about 160 thousand ids in an update and 20 thousand in a query, and traps where it does not, as before); and an old-form list of a value made to end with `|h` and the digest of its own text, which reads as an entry of the value it extends (only such a value matches; `find_by` is not affected). Known limit: a chain of old lists of `V`, `V|c`, `V|c|c` and so on deeper than about 500 raises `RecursionError` (also a value made for it). Cost, measured on the IC: a key read about 0.1 million instructions; a right entry of a value with a `|` about 0.73 million; an old entry moved about 2 million (a call of 500 moves is about 1 billion); about 0.58 million for each distinct key length in the whole map that a call walks past, so a call is O(`limit` + those lengths); an old-form list is converted whole at about 245 thousand an id, and a realm's map is not limited in value size (a list of 127,000 ids is on record: about 31 billion; it is over the cap of 100 thousand ids, so the repair leaves it, and a read of its value in an update converts it).
3. If 0.13 or 0.14 already ran on a store of 0.12 that holds the old lists of both a value `V` and `V|n` and 12 digits, or `V|h` and the digest of a custom id in `V`'s list, converting `V` wrote its entry over the other value's old list: that value's ids are lost. `Entity.rebuild_field_indexes` re-adds the sequential ones (run it, then the repair); a custom id comes back only when its entity's field is next changed, or when `Database.field_index_add` is called for it.
4. A realm that escapes its own values before indexing them (realms-gos tags: `%` as `%25`, `|` as `%7C`) holds no `|`: its keys do not change. It can keep doing so.

### Rolling back to a release before 0.15, and upgrading again

While the older release runs, a value with a `|` is filed under `_fi` (0.15 reads it there), but entries 0.15 filed under `_fx` are invisible to it, and it does not remove an `_fx` entry when an entity's value changes or the entity is deleted. After the next upgrade `count_by` and `find_by` can be wrong for such values (an entity listed that no longer has the value, an id on both lists). Run `Database.repair_field_indexes` until it returns `None`, **starting again from `after=""`** (a cursor, or a note that it was done, saved before the rollback skips what the older release wrote): it removes what is stale, moves what the older release filed, and the counts are exact again.
