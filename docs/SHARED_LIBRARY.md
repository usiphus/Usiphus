# Shared library (`usiphus_lib`)

Every engine used to carry its own copy of the same small helpers. They now live once in `usiphus_lib/`
(standard library only). The package is named `usiphus_lib`, not `usiphus`, because `usiphus.py` is the
command dispatcher.

| Module | What it provides |
|---|---|
| `errors` | `UsiphusError`, the common base of all 12 engine errors. It is still a `ValueError`, so existing `except ValueError` handlers keep working. |
| `files` | `atomic_write_text(text, destination)`, `write_json(payload, destination)`, `json_text`, and `read_json_file` with `JsonReadPolicy`. |
| `hashing` | `canonical_json`, `sha256_hex`, `digest_value` (SHA-256 of canonical JSON), `sha256_file`. |
| `text` | `clean` (trim and collapse internal whitespace) and `strip_text` (trim only). |
| `ids` | `objective_id(topic_id, index)`, e.g. `T1-LO02`. Discovery, evidence and coverage must agree on it. |
| `ollama` | `local_ollama_url`, the loopback-only Ollama address guard. |

## Conventions

* **Content first, destination second**: `atomic_write_text(text, path)`, `write_json(payload, path)`.
* **Writes are atomic and durable**: unique temp file in the target directory, `fsync`, then rename. `newline=""`
  keeps `"\n"` as `"\n"` on every platform, so hashes of generated files do not depend on the OS.
* **Hash canonical JSON, never `str(value)`**: use `digest_value` or `canonical_json`.
* **Engines raise their own error class** (subclass of `UsiphusError`) so callers can tell engines apart.

## Reading JSON: one function, per-engine policy

`read_json_file(path, policy)` holds the logic (is it a file, size limit, UTF-8 with BOM, decode errors). Each
engine keeps a small `read_json` wrapper that states *its* error class, messages and limit as a `JsonReadPolicy`,
because the engines historically reported problems differently (V17 even returns stable error codes `V17-E001`..`E004`).
The differences are now visible in one place per engine and can be unified later by editing the policy:

* V11 deliberately passes malformed JSON and bad UTF-8 through unwrapped (`invalid_on=()`).
* V12 wraps malformed JSON but lets bad UTF-8 through.
* V19 uses a 25 MiB limit, the other engines 50 MiB (V12/V13/V18) or 2 MiB (generate config).

Changing a message or limit changes behaviour: re-record `tests/data/helper_contracts.json` and say so in the commit.

## Deliberately not shared

* Time parsing (`parse_time`, `parse_timestamp`, `parse_as_of`): the engines validate different formats and fail differently.
* `usiphus_audit`, `usiphus_ledger`, `usiphus_auto_deck` and `usiphus_migrate` write JSON with their own layouts
  (no sorted keys or ASCII-escaped); changing them would change output bytes.
* `usiphus_auto_deck._checkpoint_key` hashes JSON with default separators. Making it canonical would invalidate every
  existing checkpoint.
* `usiphus_migrate._clean` treats `0` as `"0"`, unlike `text.clean`.

## Consequences to know about

* **Engines are no longer single-file scripts.** Copying one `usiphus_vNN.py` elsewhere does not work; the
  `usiphus_lib/` folder must sit next to it (as `usiphus_common.py` and `usiphus_core.py` already had to).
* **Platform behaviour**: V11, V12, V13, `usiphus_generate`, `usiphus_review` and V20 previously opened text files in
  default mode, so on Windows their output used CRLF; they now write LF like the other engines. V11, V13 and V14 also
  gained `fsync`.
* `read_json` wrappers now accept `str` paths as well as `Path`.

## Guarding the design

* `tests/test_helper_contracts.py` pins, per engine, what each helper returns, raises (type, message, code, cause) and writes.
  It was recorded before consolidation and must keep passing.
* `tests/test_architecture.py` fails if an engine defines its own copy of a shared helper, if `usiphus_lib` imports an
  engine, or if it uses a non-standard-library module.
* `tests/test_usiphus_lib.py` unit-tests the library itself.
