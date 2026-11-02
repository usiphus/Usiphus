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
* Not covered (need network/LLM/other inputs): resource discovery, auto-deck, generate, review, signing, V7–V9 pipeline.
