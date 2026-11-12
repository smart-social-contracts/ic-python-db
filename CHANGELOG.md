# Changelog

## 0.15.0 (not released)

### Fixed

- A field-index value that ends like an index key (`V1|n0000000000`) no longer hides other values' records. Its count key sat among the entries of `V1`, the page read of `V1` came back short, and `find_by` and `RelationView.page`, which take a short page for the last one, stopped: every id after it was unreachable. Any writer of a value could do it.
- A range read of a posting list goes on past keys that are not entries of its list (it already did in `_ix_all` and the purge; `_ix_page` stopped). It restarts at the last key it read instead of at the next char, which is not the next key for U+007F, U+07FF and U+FFFF, has no successor for U+10FFFF, and is a surrogate that cannot be encoded for U+D7FF.
- The entry of a custom id that reads as a JSON list is no longer taken for a list stored before 0.13 and removed.

### Changed

- **Field-index keys.** A value with a `|` in it is filed under `_fx:<Type>:<field>:<escaped value>` (`|` as `%7C`, `%` as `%25`), not under `_fi`. Every other value keeps its key, byte for byte: nothing moves for them, a value with only a `%` in it included.
- While entries of such a value may be under the old key, each `field_index_*` call also reads it, `field_index_add` and `field_index_remove` take the id off it, and a merged `field_index_get` / `field_index_page` lists an id once.

### Added

- `Database.repair_field_indexes(after="", limit=500)`: walks the field-index keys (of any length), in bounded calls. For values with a `|` it moves old entries to their present key, removes those whose entity is gone or has another value, and converts a list a release before 0.13 stored as one array.
- `Entity.rebuild_field_indexes(from_id, batch)`: `rebuild_field_index` for every indexed field of a type at once. `rebuild_field_index` moves old entries too.

### Upgrading from 0.14

1. Upgrade. Nothing is wrong meanwhile: values without a `|` are untouched, and those with one are read under both keys.
2. Run `Database.repair_field_indexes` until it returns `None`, in calls of `limit` keys. This moves every old entry, custom ids included, and converts old-form lists of values with a `|`; nothing needs to run before it (`rebuild_field_indexes` is optional). Correctness does not wait for it; it is what lets a later release drop the old-key reads, once it has run to its end. It leaves what it cannot judge: entries of a type or field the code does not index, entries whose row has no such field, and an old-form list of more than 150 thousand ids (logged; any read of its value traps converting it, as before). Cost, measured on the IC: a key read about 0.1 million instructions; a right entry of a value with a `|` about 0.73 million; an old entry moved about 2 million (a call of 500 moves is about 1 billion); about 0.58 million for each distinct key length in the whole map that a call walks past, so a call is O(`limit` + those lengths); an old-form list is converted whole at about 245 thousand an id, and a realm's map is not limited in value size (a list of 127,000 ids is on record: about 31 billion, a call of its own).
3. A realm that escapes its own values before indexing them (realms-gos tags: `%` as `%25`, `|` as `%7C`) holds no `|`: its keys do not change. It can keep doing so.

### Rolling back to a release before 0.15, and upgrading again

While the older release runs, a value with a `|` is filed under `_fi` (0.15 reads it there), but entries 0.15 filed under `_fx` are invisible to it, and it does not remove an `_fx` entry when an entity's value changes or the entity is deleted. After the next upgrade `count_by` and `find_by` can be wrong for such values (an entity listed that no longer has the value, an id on both lists). Run `Database.repair_field_indexes` until it returns `None`, **starting again from `after=""`** (a cursor, or a note that it was done, saved before the rollback skips what the older release wrote): it removes what is stale, moves what the older release filed, and the counts are exact again.
