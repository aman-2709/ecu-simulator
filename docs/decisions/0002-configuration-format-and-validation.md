# 0002 — Configuration format, YAML parser, and what validation may claim

Status: accepted 2026-09-18, Phase 4.

## Context

Phase 4 replaces the package-internal `ecu_config.json` with YAML profiles validated by a
schema, and corrects two deviations: malformed DTC strings are silently skipped by the
encoder (DEV-13), and invalid configuration values either call `exit(1)`, are silently
replaced by a default, or pass validation and raise `OverflowError` at request time
(DEV-14). The point of both corrections is that a malformed profile is rejected at load
with a path-qualified message instead of failing silently or later.

That goal makes the parser choice a correctness decision, not a matter of taste: a parser
that silently discards input undermines the phase.

## Parser decision

**Chosen: `ruamel.yaml` 0.19.x, loaded through `YAML(typ="safe", pure=True)`.**

Rejected: `PyYAML` 6.0.3 via `yaml.safe_load`.

Both were installed and compared experimentally on the project's Python 3.12.12 against
one sample document. Observed behavior:

| Input | PyYAML 6.0.3 (YAML 1.1) | ruamel.yaml 0.19.1 (YAML 1.2 safe) |
|---|---|---|
| `enabled: no` | `False` (bool) | `'no'` (str) |
| `mode: on` | `True` (bool) | `'on'` (str) |
| `odometer: 12:30` | `750` (int, sexagesimal) | `'12:30'` (str) |
| `obd_physical: 0x7E0` | `2016` (int) | `2016` (int) |
| `version: 1.2.3` | `'1.2.3'` (str) | `'1.2.3'` (str) |
| duplicate key `a:` twice | `{'a': 2}`, last wins silently | `DuplicateKeyError` |
| `!!python/object/apply:os.system` | refused (`ConstructorError`) | refused (`ConstructorError`) |

Reasons, against the criteria set for this decision:

- **Safe loading.** Both refuse arbitrary object construction. Neither generic loader
  (`yaml.load` with `Loader=yaml.Loader`, `YAML(typ="unsafe")`) is used anywhere, and that
  is enforced by a test.
- **Silent data loss.** PyYAML accepts a duplicate key and keeps the last occurrence with
  no diagnostic. For a phase whose purpose is to reject malformed configuration explicitly,
  a duplicate `ecus:` entry or a repeated address silently disappearing is the exact
  failure mode being removed. ruamel raises. This is the decisive reason.
- **Surprising coercions.** YAML 1.1 turns `no` and `on` into booleans and reads
  colon-separated values as sexagesimal integers. An ECU named `no`, or any future
  colon-bearing value, would be corrupted before the schema ever sees it. YAML 1.2's core
  schema does not do either.
- **Normal type preservation.** Both preserve strings, integers, floats and lists as
  expected otherwise, including hexadecimal integers, which lets CAN identifiers be written
  as `0x7E0` and arrive as integers.
- **Dependency weight.** ruamel.yaml 0.19.1 declares no mandatory dependencies; the C
  extension and the round-trip and jinja2 support are optional extras. `pure=True` keeps it
  on the pure-Python path. Weight is therefore comparable to PyYAML's.
- **Python support.** Both publish support for 3.9 through 3.14, covering this project's
  3.12 and 3.13.
- **Maintenance.** ruamel.yaml 0.19.1 was released 2026-01-02, PyYAML 6.0.3 on 2025-09-25.
  Both are maintained. ruamel.yaml has a smaller maintainer base, which is the main cost
  accepted here; it is mitigated by using only the documented safe-loading entry point, so
  replacing it later would touch one module.

Round-trip preservation, ruamel's headline feature, is explicitly not used. The simulator
reads profiles and never rewrites them.

## What the validation may claim

The schema rejects malformed input. It does not validate against any specification.

- **DTC strings (DEV-13).** The accepted representation is the one the existing encoder
  already accepts, which is pinned byte-for-byte by the Phase 0 characterization tests.
  Validation moves that same grammar to load time so a malformed entry is reported with its
  path instead of being silently skipped. The authority for the rule is this project's own
  pinned behavior, not SAE J2012, whose text has not been reviewed. This is project input
  validation and is described as such.
- **VIN, fuel level, fuel type, CAN identifiers (DEV-14).** Same principle. Length and
  range constraints come from the ranges the existing encoders can represent and from
  publicly documented conventions; none is a conformance claim. CAN identifier ranges are
  already enforced in the transport and were confirmed experimentally against the kernel in
  Phase 2A.

Accordingly no row in `docs/conformance.md` gains `standards validated` from this phase.

## Consequences

- `ruamel.yaml>=0.19,<0.20` and `pydantic>=2.13,<3` become runtime dependencies. The
  inventory in `docs/modernization-plan.md` section 7.2 records the versions reviewed.
- Profiles are read with one loader helper so the parser is replaceable in one place.
- Configuration errors are reported with the path to the offending key and never with a
  bare exception or `exit(1)`.
