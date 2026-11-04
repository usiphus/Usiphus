# Golden-output tests

Characterization tests that pin what every engine (V10–V20) actually writes, so a
refactor that changes behaviour fails loudly. Each scenario runs the engine through its
real CLI on small synthetic fixtures (or the shipped `examples/`) and compares every
output file with `expected/<scenario>/`.

    python -m unittest tests.test_golden -v                 # check
    UPDATE_GOLDEN=1 python -m unittest tests.test_golden    # re-record, then review `git diff`

Only **volatile** data is normalised: the temp work dir (`<WORK>`, in values *and* dict keys),
wall-clock `generated_at` in V12/V13/V14 (`<VOLATILE>`), and SHA-256 values that point at a file
containing such data. Everything else, including exit codes and the list of files written, is exact.
`Determinism` tests also run each scenario under two `PYTHONHASHSEED`s and require identical output.

## Rules of thumb
* A golden diff is a *behaviour change*. If intended, re-record and explain it in the commit.
* Fixtures are frozen inputs: `fixtures/v12_output/` is a saved V12 run used as V13's input, and
  `fixtures/{learner_progress,cohort_analytics}.json` / `review_schedule.csv` are saved V11 output.
  **If you edit the seven shared fixture artifacts, regenerate `fixtures/v12_output/`** (V13 checks that
  the V12 outputs match the inputs), then re-record.
* `fixtures/v15_quorum/claims.json` contains hash-chained audit events built once with `usiphus_v15.build_event`.
* `.gitattributes` pins LF endings for fixtures/examples/snapshots because input hashes and byte counts are recorded.

## Stubbed scenarios (network, LLM, keys, TTY)
Engines that talk to other systems run through `stubs/runner.py`, which executes the engine's *real* CLI as
`__main__` but first **blocks all sockets** (an unstubbed request raises instead of reaching the internet) and
installs a fake from `stubs/stub_<name>.py`:

| stub | replaces | used by |
|---|---|---|
| `discovery` | NCBI E-utilities, Europe PMC, Crossref (synthetic records, one empty topic, two provider failures) | `usiphus_resource_discovery` |
| `deck`, `deck_lowquality` | local Ollama `/api/chat` (+ fake `genanki`) | `usiphus_auto_deck` |
| `generate`, `generate_hallucinated` | local Ollama `/api/generate` | `usiphus_generate` |
| `anki` | fake `genanki` + AnkiConnect on `localhost:8765` | `usiphus_v7` / `v8` / `v9` |

* Every outgoing request (method, URL, body) is recorded and snapshotted as `_requests.json`, so a refactor
  that changes a prompt, query parameter or AnkiConnect payload fails the test.
* `fake_genanki` writes the deck/model/notes it receives as JSON where the `.apkg` would go: it pins the Anki
  export *inputs* without needing `genanki`. It does not validate real `.apkg` files.
* `.pptx` output is compared as extracted slide text (zip timestamps ignored). The scenario is skipped when
  `python-pptx` is not installed (`requires=`), as is the signing scenario without `cryptography` or OpenSSL 3.
* Interactive `usiphus_review` runs with scripted stdin. Multi-step flows (`usiphus_audit`) record a transcript
  of every step's label, exit code, stdout and stderr in `_transcript.txt`; the final step's exit code is the scenario's.
* The audit scenario uses a **throwaway TEST-ONLY Ed25519 key** (`fixtures/audit/TEST_ONLY_alice.key.b64`, base64 so
  it is not a bare PEM that secret scanners flag). It protects nothing. Ed25519 signatures are deterministic, so
  event signatures are pinned; only the anchor file's `generated_at`/`signature` are masked.
* Log timestamps in captured stderr are replaced with `<TS>`.
* Still not covered: `keygen` (random by design), AnkiConnect against a real Anki, real Ollama/provider behaviour,
  real `genanki` `.apkg` bytes, `extract_python.py`, `usiphus_curriculum_coverage` CLI, `usiphus_migrate`/`usiphus_ledger`.
