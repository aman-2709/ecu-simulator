# Phase 7 acceptance evidence

Raw artefacts behind [../phase-7-acceptance.md](../phase-7-acceptance.md), kept here so the
report's quoted excerpts can be checked against the complete capture rather than taken on
trust. All three were produced on **2026-09-22** at revision
`08698d299a1d9c1a41743c2ccac84cf6bca15d69`.

| File | What it is |
|---|---|
| `scenario-run.candump.log` | `candump -L` capture of the full 125-second `ice_scenario.yaml` run: 46 frames covering the whole drive, the fault going pending then confirmed, the clear, and the checks at 125 s |
| `scenario-run-results.json` | The 23 checks of that same run, each with its configured and actual timestamp, request, expected bytes, observed bytes and verdict. All 23 passed |
| `uds-default-profile.candump.log` | `candump -L` capture of the UDS verification on `ice_default.yaml`: TesterPresent, the three suppressed requests, the negative responses that survive suppression, and the multi-frame `19 02 FF` with its flow control |

## Provenance

**These are from the scripted acceptance run**, performed while writing the report using
`scripts/acceptance/phase7_scenario_acceptance.py` and a `candump` capture alongside it.

**No artefact from the reviewer's manual session of 2026-09-22 was available**, so none
appears here. The reviewer's run is recorded in the report by its reported results —
10/10 on UDS and 21/21 on the scenario — and nothing has been reconstructed to stand in
for evidence that was not kept. The two runs agree at every checkpoint they share, which
is stated in the report as agreement between two independent runs and not as a single
merged result.

## Reading the captures

`candump -L` format is `(epoch.microseconds) interface canid#data`. The report renders the
same frames with timestamps relative to the first frame, and annotates them; the raw files
are unedited.

Reproduce either capture with:

```bash
scripts/acceptance/run_phase7_acceptance.sh --trace /tmp/scenario.log --json /tmp/results.json
```

Byte-for-byte equality with the files here is not expected and is not the point: the
timestamps are wall-clock, and a value sitting on a ramp depends on the millisecond the
request arrived. What should reproduce is every assertion the report makes.
