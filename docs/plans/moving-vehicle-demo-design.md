# Moving-vehicle demo profile and the smallest timeline extension — design

Status: **proposal, awaiting the owner's decision. Nothing is implemented.** This file is
the only change. No `src/`, `tests/` or profile file was touched.

Written on branch `gui` (worktree `.claude/worktrees/gui`). Every "fact" below was
re-verified against the code on this branch, with file:line. Everything under a
"Proposal" heading is a proposal and not a fact about the code. The OBD bytes in §8 were
computed in-process by the simulator's own schema, runner, dispatcher and encoders, with no
CAN (Appendix A).

## 1. Scope and status

- **Asked for:**
  - a separate moving-vehicle demo profile;
  - the smallest timeline extension that makes it readable (`interpolate`, `repeat`);
  - an odometer that never decreases, or none at all;
  - a way to show an unsourced odometer as unavailable in the GUI.
- **Not asked for, and not proposed:** a looping odometer ramp, any change to
  `ice_default.yaml` or `ice_scenario.yaml`, any new OBD PID, conditions or triggers, or
  randomness.
- **Stop point.** This design stops for review. No scenario or timeline code is written
  until the owner approves this design, including the branch and gate question in §2.
- **Unaffected and still open:**
  - The M2 early-check latency `STOP` stays open and not accepted
    (`docs/decisions/0010-gui-observer-api.md:8-10`,
    `docs/validation/gui-m2-early-check.md`). Nothing here measures or changes it.
  - The hosted-CI `CAN_ISOTP` skips stay as they are. GitHub-hosted runners have no
    `can_isotp` (`docs/modernization-plan.md:342-349`), and decision 0009 proposes the
    runner that would fix that. Every vcan test proposed here is skipped on hosted CI in
    the same way.

## 2. Where this work would sit (fact, then options for the owner)

Facts:

- The scenario engine is V1.0 code. Phase 7 is listed under V1.0
  (`docs/modernization-plan.md:123`, `:249`). It was approved with rulings in 0006, and one
  ruling is **"Exactly the six generators, pure functions of `t`"**
  (`docs/decisions/0006-phase-7-scenario-and-testerpresent.md:523`).
- In 0006 §5.1, the timeline is defined as step-and-hold ("the value of the latest point
  whose `at <= t`") (`0006:310`). 0006 also calls the timeline "absolute", in contrast to
  `stepped`, which repeats (`0006:312`).
- The conformance row is "Six deterministic generators as pure functions of elapsed time"
  (`docs/conformance.md:217`).
- V1.0 is not tagged without Phase 8b's evidence. Phases 9 to 11 may proceed while 8b is
  open (`docs/modernization-plan.md:364-365`).
- Branch `gui` starts from `modernization` at `a57b98f`, and it is **not merged into
  `modernization` until V1.0 is tagged**. Nothing on `gui` changes V1.0 scope, the 8b gate
  or any conformance status (`0010:14-18`, `docs/modernization-plan.md:567-577`).
- The standing gates apply to any change: the phase completion gate
  (`docs/modernization-plan.md:694-756`) and the documentation and standards verification
  gate (`:758-`). The timeline extension depends on no external standard. Python float
  semantics are the only external behaviour it relies on (§3.5).

**Consequence.** `interpolate` and `repeat` change the semantics of a V1.0 generator, so
they amend 0006 §5.1 and 5.3. A `distance` generator (§6) would be a seventh generator,
which reverses the "exactly six" ruling. Neither can happen without the owner's approval.

The options, not decided here:

| Option | Where the timeline change lands | Gate or approval it needs | Cost |
|---|---|---|---|
| A | `modernization`, now, as an amendment to Phase 7 | The owner's approval; an amendment to 0006, or a new decision record; the phase completion gate (§10) evidence for the amended rows; `conformance.md` wording | It adds scope to V1.0 after Phase 7 closed and before 8b. The defaults preserve every byte (§4), but it is still V1.0 code changing |
| B | `modernization`, as a small post-8a item beside Phases 9 to 11 (V1.1) | The same decision record. It is not on the V1.0 path | The demo waits for that branch, and `gui` rebases or merges it later |
| C | `gui` only | Owner approval. It contradicts the `gui` rule that V1.0 code is not changed there (`0010:14-18`) | Divergence in V1.0 files that the eventual merge must reconcile |

The demo profile is packaged with the wheel (`pyproject.toml:63`, profiles glob). It goes
wherever the extension goes, because it cannot be written without it (§5).

The GUI change for "unavailable" (§7, recommended option) touches only `gui` files and 0010.
It does not depend on the timeline decision.

## 3. The timeline extension

### 3.1 Today (fact)

- `TimelineSignal` has `type` and `points`. Points must ascend by `at`, and **equal `at`
  values are allowed**: the check is `times != sorted(times)`
  (`src/ecu_simulator/scenario/generators.py:141-153`).
- `value_at(t)` returns the value of the last point whose `at <= t`, and the first point's
  value before the first point (`generators.py:155-161`). It never interpolates, and it
  holds the last value forever.
- Generator models forbid unknown keys (`generators.py:34`). Today, `interpolate:` or
  `repeat:` on a timeline is refused as `Extra inputs are not permitted` (Appendix A.3).
- Scenario time `t` is a Python `float`. `ScenarioSync.elapsed` is
  `clock.now() - origin` (`scenario/sync.py:31-32`). The origin is read once at build
  (`sync.py:25`, `app.py:222-224`). `MonotonicClock` returns `time.monotonic()`
  (`clock.py:24-25`). `SimulatedClock.advance` accumulates `self._now += float(seconds)`
  (`clock.py:37-41`). So `t` is an arbitrary double: exact for whole or dyadic advances in
  tests, and not exact in production.
- The runner calls `value_at(t)` for every signal and writes `int(value)` into
  integer-typed signals (`scenario/runner.py:148-151`). The integer or float decision is
  made once, from the vehicle's initial value types (`runner.py:107-110`). A `t` earlier
  than the last applied one is refused with a warning (`runner.py:136-143`).

### 3.2 Proposal: two optional fields on `timeline` only

```yaml
- path: vehicle.speed
  type: timeline
  interpolate: linear   # step | linear; default step
  repeat: 90            # seconds; default absent, meaning no repeat
  points: [...]
```

- `interpolate: Literal["step", "linear"] = "step"`.
- `repeat: float | None = Field(default=None, gt=0)`.
- When both are absent, or `interpolate: step` with no `repeat`, `value_at` runs the
  **existing code path unchanged**. §4 proves it.
- No other generator gains either field. `repeat` on a `ramp` stays
  `Extra inputs are not permitted`.

### 3.3 Linear: the value at `tc`

`tc` is the evaluation time. Without `repeat`, `tc = t`. With `repeat`, `tc` is the cycle
time from §3.4. Let the points be `(a0, v0) … (an, vn)`, ascending.

| Case | Value |
|---|---|
| `tc < a0` (before the first point) | `v0`, held. This is the same as step today (`generators.py:156`) |
| `ai <= tc < ai+1`, with `i` the **last** index where `ai <= tc` | `vi + ((vi+1 − vi) × (tc − ai)) / (ai+1 − ai)` |
| `tc >= an` (at or after the last point) | `vn`, held. This is the same as step |
| `tc == ai` exactly | `vi` exactly, because the fraction is 0 |
| Two points share one `at` (allowed today) | Choosing the last index with `ai <= tc` never selects a zero-width segment, so there is no division by zero. The value jumps at that `at`, and the later point wins, exactly as step does today |

- **Multiply before divide.** The expression is evaluated in that order. With integer
  knots and a time where the exact result is a whole number, the numerator is exact and
  IEEE division is correctly rounded, so the result is that whole number exactly. The
  demo relies on this: 1960 rpm at t=12 is `1960.0`, not `1959.9999…`.
- Only `+ − × ÷` are used, with no libm. So unlike `sine` (`generators.py:79-83`,
  `0006:315-321`), a linear timeline gives the same bits on any IEEE-754 platform for the
  same double `t`.
- **Non-repeating linear:** before `a0`, hold `v0`. After `an`, hold `vn` forever.
- **Repeating linear:** the same table, applied to `tc = cycle time`. §3.4's rules make
  `a0 = 0` and `an = repeat`, so "before the first point" cannot happen. "At or after the
  last point" happens only at `tc == repeat`, which the mapping never produces.

### 3.4 Repeat: the cycle time, and the validation rule

**Mapping.** `tc = math.fmod(max(t, 0.0), repeat)`.

- `t < 0` never happens in the runtime, because the origin is captured at build. It is
  treated as 0 and gives `v0`. This matches `ramp` and `stepped`, which treat `t <= 0` as
  the start (`generators.py:69, 104`).
- `math.fmod` is used rather than `%` so that the rule is one named function. For
  `t >= 0` and `repeat > 0` the two agree.

**Validation. The rule chosen is the "closing point".** When `repeat` is set:

1. the first point's `at` must be `0`;
2. the last point's `at` must equal `repeat`;
3. the last point's `value` must equal the first point's `value`.

Why this rule, and not "`repeat` must be greater than the last point's `at`":

- The file states the value at the boundary explicitly. The closing point *is* the
  value at `t = k × repeat`, in both step and linear mode. No segment is left implicit.
- **Linear is continuous at the wrap by construction.** Under the alternative, the stretch
  between the last point and `repeat` would need a rule of its own: hold then jump, or
  interpolate back to `v0`. Either rule is invisible in the file, and forgetting it gives
  a silent jump. Under the closing-point rule, a jump has to be written as two points at
  the same `at`, so it is visible.
- It is the same idea as `stepped` wrapping to `values[0]` (`generators.py:103-105`), made
  explicit.
- The cost is one extra line per signal, and a closing point that step mode never
  evaluates (it equals `v0` anyway).

### 3.5 The loop boundary, exactly

Here `k >= 1` is an integer, and `ε` is small: in the test, one ulp or 0.001 s.

| `t` | `tc` | step | linear |
|---|---|---|---|
| `k·repeat − ε` | `repeat − ε` | the value of the last point with `at <= repeat − ε`, which is **the point before the closing point** | `→ vn = v0` as `ε → 0` (continuous) |
| `k·repeat` exactly | `0.0` exactly | `v0` | `v0` |
| `k·repeat + ε` | `ε` | `v0` (until the next point's `at`) | `v0 + slope0 × ε` |

- **Linear wraps continuously.** The left limit is `vn`, which the rule makes equal to
  `v0`, and the value at the boundary is `v0`.
- **Step jumps at the boundary.** It jumps from the second-to-last value to `v0`, as step
  already jumps at every point.
- Worked example, with points `(0,10) (30,20) (60,10)` and `repeat: 60` (Appendix A.2):

| t | tc | step | linear |
|---|---|---|---|
| 29.999 | 29.999 | 10 | 19.999666… |
| 30.0 | 30.0 | 20 | 20.0 |
| 59.999 | 59.999 | 20 | 10.000333… |
| 59.99999999999999 (one ulp below 60) | 59.99999999999999 | 20 | 10.000000000000002 |
| 60.0 | 0.0 | **10** | **10.0** |
| 60.001 | 0.000999999999997669 | 10 | 10.000333… |
| 90.0 | 30.0 | 20 | 20.0 |
| 120.0 | 0.0 | 10 | 10.0 |

### 3.6 Floating point: what "deterministic at exact multiples" means here

- **`math.fmod` is exact.** IEEE-754 and C99 `fmod` return the exact remainder with no
  rounding. So a double `t` that equals `k × repeat` exactly gives `tc == 0.0` exactly:
  `fmod(270.0, 90.0) == 0.0`, and `fmod(9e10, 90.0) == 0.0` (Appendix A.2).
- **The values are a pure function of the double `t`,** and nothing else. Two calls, two
  processes or two machines with the same double `t` give the same value and the same
  bytes.
- **What is not promised.** A decimal time and the "same" decimal time one cycle later
  are different doubles. For example, `105.3 − 90` in doubles is
  `15.299999999999997`, not `15.3`. Their values can then differ in the last bit, and the
  runner's truncation can turn that into a different byte (§8.3: 2454 rpm at t=15.3,
  2455 rpm at t=105.3). That is deterministic, but it is not byte-periodic.
  - Tests assert periodicity only at exactly representable times: whole seconds, or
    halves and quarters.
  - The same hazard exists today for `ramp` and at every truncation boundary. It is not
    new.
- **Rejected alternatives:**
  - Quantising `tc` (for example, rounding to 1 ms) adds a second rule and a new boundary.
  - Rational `t` would need the clock seam to change. The runner receives a float
    (`runner.py:126`, `sync.py:35`).

### 3.7 Other generators

`constant`, `ramp`, `sine`, `stepped` and `sequence` are unchanged: code, parameters and
tests (`generators.py:50-131`). `stepped` already repeats and `sine` is already periodic.
Neither gains `repeat`, and the six-generator set test stays exactly as written
(`tests/unit/test_scenario_generators.py:42-44`).

### 3.8 Schema validation errors (proposed exact text)

The rule is a model validator on `TimelineSignal`. Its messages start with `repeat: `, so
`config/schema.py`'s `_join` moves the sub-path into the location
(`config/schema.py:354-368`), as the existing validators do. Several problems are joined
with `; `, following the rule at `schema.py:357-360`.

| Problem | Message as `validate-config` prints it |
|---|---|
| First point not at 0 | `scenario.signals.0.timeline.repeat: a repeating timeline must start its cycle at 0, but its first point is at 5.0` |
| Last point not at `repeat` (before or after it) | `scenario.signals.0.timeline.repeat: a repeating timeline must end with a point at its repeat time 90.0, but its last point is at 75.0` |
| The closing value differs | `scenario.signals.0.timeline.repeat: the last point closes the cycle, so its value must equal the first point's value 0.0, got 5.0` |
| `repeat: 0` or negative | `scenario.signals.0.timeline.repeat: Input should be greater than 0` (Pydantic's own, as `ramp.over` today) |
| `interpolate: cubic` | `scenario.signals.0.timeline.interpolate: Input should be 'step' or 'linear'` |
| `repeat` on another generator | `scenario.signals.1.ramp.repeat: Extra inputs are not permitted` (unchanged) |

The Pydantic messages above were checked against Pydantic 2.13.5 (Appendix A.3). The
first three are proposed text, in the style of
`timeline points must be in ascending 'at' order, got [...]` (`generators.py:152`).

## 4. Determinism and the regression proof

### 4.1 Invariants kept (0006 §5, `runner.py:8-22`)

- A generator is still a pure function of `t`. It holds no state, reads no clock and
  knows no other signal.
- `ScenarioRunner.apply` is untouched. It stays the only writer, it is idempotent for a
  fixed `t`, and it performs no await.
- **Events never repeat.** `repeat` belongs to one timeline. DTC events are consumed once
  by their applied-marker (`runner.py:153-158`), so a repeating drive cycle never
  re-raises a code.

### 4.2 The proof that existing scenarios keep their current behaviour

The owner's pin-then-transition method is used. **Commit 1 pins the goldens against the
unchanged code**, and commit 2 changes the generator. Commit 1 must pass before commit 2
exists.

1. **The golden values of `ice_scenario.yaml` at many timestamps** (new file
   `tests/unit/test_scenario_goldens.py`, recorded before the change):
   - Timestamps:
     - every 0.5 s from 0 to 130 s;
     - each timeline `at` in the profile, and one ulp either side of it
       (`math.nextafter`);
     - 600 s, 3600 s and 86400 s.
   - Per timestamp, recorded exactly:
     - the stored values of `vehicle.speed`, `engine.rpm`, `engine.coolant_temp`,
       `engine.throttle` and `engine.fuel_level`;
     - the replies to `01 0C 0D 11 05 2F`, `03`, and `19 02 FF` on `0x7E1`.
   - `engine.engine_load` is driven by `sine`, so it is compared with
     `math.isclose(rel_tol=1e-12)` and PID `0x04` is left out of the byte goldens. This is
     0006's own rule, that byte-exact assertions avoid libm (`0006:315-321`).
2. **Absent means identical, as a differential test**
   (`test_scenario_goldens.py::test_an_explicit_step_timeline_answers_byte_for_byte_like_an_absent_one`):
   - Load `ice_scenario.yaml` as shipped, and a copy with `interpolate: step` added to both
     timelines.
   - At every golden timestamp, the OBD replies are identical, and so is
     `vehicle.signals`.
3. **The old function, frozen**
   (`test_scenario_generators.py::test_a_timeline_without_the_new_fields_matches_the_phase_7_rule`):
   - The Phase 7 `value_at` is copied verbatim into the test as `phase7_value_at`.
   - For every timeline in the shipped profile and in the existing tests, at the golden
     grid plus ±1 ulp around every `at`, `value_at(t) == phase7_value_at(points, t)`.
4. **Existing test files that must stay green, unmodified:**

| File | Why it matters |
|---|---|
| `tests/unit/test_scenario_profile.py` | pins `ice_scenario.yaml`'s driven paths (`:35-45`), speed bytes at eight instants (`:65-73`), coolant bytes (`:76-85`), multi-PID bytes (`:96-103`), events and replay |
| `tests/unit/test_scenario_generators.py` | "exactly six generators" (`:42-44`); step timeline semantics (`:202-232`); no transcendental function in the byte-exact generators (`:236-249`) |
| `tests/unit/test_scenario_schema.py` | the refusal of bad timelines (`:105-132`), and unknown keys (`:135`) |
| `tests/unit/test_scenario_runner.py`, `tests/unit/test_scenario_wiring.py` | runner invariants: idempotence, events once, backward time, no await |
| `tests/characterization/*` (`test_obd_golden.py`, `test_uds_golden.py`, `test_dtc_golden.py`, `test_config_golden.py`, `test_mode09_pid0a_frozen.py`) | `ice_default.yaml`, which has no scenario and builds no runner (`conformance.md:252-254`) |
| `tests/unit/observe/test_snapshots.py` | `/vehicle` and `state` over `ice_scenario.yaml` |
| `tests/integration/test_scenario_isotp.py` | a step timeline on vcan. Skipped on hosted CI (§1) and run on a vcan host |

Note, fact: the comment in `tests/unit/test_scenario_schema.py:229` says "Unlike a
timeline, which is read by interpolation". A timeline is not read by interpolation today
(`generators.py:155-161`). It is proposed to correct that comment in the implementation.
The test itself is unaffected.

### 4.3 New unit tests (names and assertions)

In `tests/unit/test_scenario_generators.py`:

| Test | Assertion |
|---|---|
| `test_a_timeline_defaults_to_step_without_repeat` | a built timeline has `interpolate == "step"` and `repeat is None` |
| `test_a_linear_timeline_interpolates_between_points` | points `(0,0) (10,100)`: `value_at(2.5) == 25.0`, `value_at(5) == 50.0` |
| `test_a_linear_timeline_lands_exactly_on_its_points` | `value_at(ai) == vi` for every point |
| `test_a_linear_timeline_holds_its_first_value_before_the_first_point` | points `(5,10) (10,20)`: `value_at(0) == value_at(4.999) == 10` |
| `test_a_linear_timeline_holds_its_last_value_after_the_last_point` | the same points: `value_at(10) == value_at(1e6) == 20` |
| `test_two_points_at_one_time_make_a_linear_jump` | `(0,0) (10,50) (10,0) (20,0)`: `value_at(9.999) ≈ 49.995`, `value_at(10) == 0` |
| `test_linear_interpolation_multiplies_before_dividing` | `(9,1120) (15,2800)`: `value_at(12) == 1960.0` exactly |
| `test_repeat_maps_time_to_cycle_time_in_step_mode` | `(0,10) (30,20) (60,10)`, repeat 60: at `29.999, 30, 59.999, 60, 60.001, 90, 120` the values are `10, 20, 20, 10, 10, 20, 10` |
| `test_a_linear_repeat_is_continuous_at_the_boundary` | the same points, linear: `value_at(60.0) == 10.0` and `abs(value_at(nextafter(60,0)) − 10.0) < 1e-9` |
| `test_repeat_at_exact_multiples_gives_the_first_value` | for `k` in 1…1000, and for `k = 10**9`: `value_at(k * 60.0) == 10` |
| `test_repeat_is_periodic_at_exactly_representable_times` | for `t` in steps of 0.25 over one cycle, and `k` in 1…100: `value_at(t + k*60.0) == value_at(t)` |
| `test_a_time_before_zero_is_the_first_value_of_a_repeating_timeline` | `value_at(-1.0) == 10` |
| `test_a_repeating_timeline_must_start_at_zero` | `ValidationError`, with the §3.8 text |
| `test_a_repeating_timeline_must_end_at_its_repeat_time` | the last `at` below, and above, `repeat`: the §3.8 text |
| `test_a_repeating_timeline_must_end_where_it_began` | the §3.8 text |
| `test_repeat_must_be_positive` | `0` and `-5` are refused |
| `test_interpolate_is_step_or_linear` | `cubic` is refused |
| `test_only_a_timeline_takes_interpolate_or_repeat` | for each of the other five types, `repeat` and `interpolate` are refused as extra inputs |
| `test_a_linear_timeline_uses_no_transcendental_function` | the byte-exact entry list gains a linear repeating timeline whose value at `t=7.0` is integral (extends `:236-249`) |

In `tests/unit/test_scenario_schema.py`:

| Test | Assertion |
|---|---|
| `test_a_bad_repeat_is_reported_with_its_path` | the `ConfigError` text contains `scenario.signals.0.timeline.repeat: a repeating timeline must end with a point at its repeat time 90.0, but its last point is at 75.0` |
| `test_every_repeat_problem_is_reported_at_once` | all three rule failures appear in one error |

In `tests/unit/test_scenario_runner.py`:

| Test | Assertion |
|---|---|
| `test_a_linear_value_is_truncated_into_an_integer_signal` | a linear rpm timeline gives `2454.999999999999` at t=15.3, and `engine.rpm == 2454`. This records §3.6 and changes nothing |
| `test_a_repeating_timeline_does_not_repeat_events` | a repeat-60 timeline and one event at 40: after `apply(100)` and `apply(160)`, `pending_events == 0` and the store was updated once |

## 5. The moving-vehicle demo profile (proposal)

### 5.1 A new file, not an edit

- Proposed name: **`src/ecu_simulator/profiles/ice_drive_cycle.yaml`**.
- Editing `ice_scenario.yaml` would break existing tests (fact):
  - `tests/unit/test_scenario_profile.py:35-45` pins its six driven paths;
  - `:65-73` pins speed bytes at eight instants (for example `410d1e` at t=20);
  - `:76-85` pins the coolant bytes;
  - `:96-103` pins the multi-PID reply at 45 s;
  - `:115-123` pins event timing;
  - `tests/unit/observe/test_snapshots.py:20-70` builds it;
  - it is also the Phase 7 manual acceptance subject (`conformance.md:242-249`), and
    `README.md:185` names it.
- `ice_default.yaml` stays scenario-free (`conformance.md:252-254`).

### 5.2 The cycle

- 90 s, `repeat: 90`, `interpolate: linear`.
- Idle, pull away, accelerate in 2nd gear, upshift, top gear, cruise, brake with engine
  braking, clutch in, idle.
- Two gear ratios keep rpm consistent with speed.
- Where rpm and speed have knots at the same times and `rpm = ratio × speed` at both ends,
  linear interpolation keeps that ratio at every instant in between.

| Phase | Cycle time (s) | Speed (km/h) | rpm | Throttle (%) | Load (%) |
|---|---|---|---|---|---|
| Idle | 0–5 | 0 | 800 | 0 | 20 |
| Pull away, clutch slipping | 5–9 | 0 → 20 | 800 → 1120 | 0 → 45 by 6 s | 20 → 75 by 6 s |
| 2nd gear, 56 rpm per km/h | 9–15 | 20 → 50 | 1120 → 2800 | 45 | 75 |
| Upshift | 15–16 | 50 → 55 | 2800 → 1650 | 45 | 75 |
| Top gear, 30 rpm per km/h | 16–21 | 55 → 80 | 1650 → 2400 | 45 | 75 |
| Cruise | 21–60 | 80 | 2400 | 45 → 18 by 22 s, then 18 | 75 → 35 by 22 s, then 35 |
| Brake, engine braking in top gear | 60–70 | 80 → 26.7 | 2400 → 800 | 18 → 0 by 60 s | 10 (overrun) |
| Clutch in, rolling to a stop | 70–75 | 26.7 → 0 | 800 | 0 | 10 → 20 by 71 s |
| Idle | 75–90 | 0 | 800 | 0 | 20 |

- Acceleration is 5 km/h per second. Braking is 5.33 km/h per second, about 1.5 m/s².
- The coolant warm-up **does not repeat**. It uses the existing `ramp` from 20 °C to 90 °C
  over 240 s, which then holds (`generators.py:60-73`). It is still rising through the
  first two cycles and reaches 90 °C during the third cycle.
- Stated simplifications:
  - the shift skips from 2nd to top gear;
  - the throttle is not lifted during the shift;
  - MAF, MAP, intake temperature and timing advance stay at their configured idle
    values. §9 asks whether to drive them.

### 5.3 The proposed file (scenario part)

The `vehicle` block and the `ecus` block are as in `ice_scenario.yaml`
(`ice_scenario.yaml:30-52, 121-170`): `rpm: 800`, `coolant_temp: 20`, the same endpoints,
and the same three trouble codes. There are **no `dtc_events`** (§9).

```yaml
scenario:
  tick: 0.5
  signals:
    - path: vehicle.speed
      type: timeline
      interpolate: linear
      repeat: 90
      points:
        - {at: 0,  value: 0}     # idle
        - {at: 5,  value: 0}     # pull away
        - {at: 21, value: 80}    # 5 km/h per second
        - {at: 60, value: 80}    # cruise
        - {at: 75, value: 0}     # brake
        - {at: 90, value: 0}     # closes the cycle
    - path: engine.rpm
      type: timeline
      interpolate: linear
      repeat: 90
      points:
        - {at: 0,  value: 800}
        - {at: 5,  value: 800}
        - {at: 9,  value: 1120}  # clutch engaged at 20 km/h in 2nd, 56 rpm per km/h
        - {at: 15, value: 2800}  # 50 km/h in 2nd
        - {at: 16, value: 1650}  # upshift: 55 km/h in top, 30 rpm per km/h
        - {at: 21, value: 2400}  # 80 km/h in top
        - {at: 60, value: 2400}
        - {at: 70, value: 800}   # engine braking to 26.7 km/h, then clutch in
        - {at: 90, value: 800}
    - path: engine.throttle
      type: timeline
      interpolate: linear
      repeat: 90
      points:
        - {at: 0,  value: 0}
        - {at: 5,  value: 0}
        - {at: 6,  value: 45}
        - {at: 21, value: 45}
        - {at: 22, value: 18}
        - {at: 59, value: 18}
        - {at: 60, value: 0}
        - {at: 90, value: 0}
    - path: engine.engine_load
      type: timeline
      interpolate: linear
      repeat: 90
      points:
        - {at: 0,  value: 20}
        - {at: 5,  value: 20}
        - {at: 6,  value: 75}
        - {at: 21, value: 75}
        - {at: 22, value: 35}
        - {at: 59, value: 35}
        - {at: 60, value: 10}    # overrun
        - {at: 70, value: 10}
        - {at: 71, value: 20}
        - {at: 90, value: 20}
    # Warm-up happens once. Not a repeating timeline: the engine does not cool between laps.
    - path: engine.coolant_temp
      type: ramp
      from: 20
      to: 90
      over: 240
```

## 6. Odometer and distance

### 6.1 Facts

- `CommonState.odometer: int = 0  # km` (`vehicle/state.py:27`).
- A profile cannot set it:
  - `VehicleConfig` has no `odometer` field (`config/schema.py:84-92`);
  - unknown keys are refused (`schema.py:51-54`);
  - `build_vehicle` does not pass it (`app.py:97-103`).
- Nothing drives it. It is not in either shipped scenario.
- No PID reads it. No `MODE01_DEFINITIONS` entry lists `vehicle.odometer`
  (`protocols/obd/pids.py:114-268`), and 0x31 and 0xA6 are not in the table.
- A scenario *may* drive it today. `signal_types` reports it as `int`
  (`state.py:123-136`), and the schema accepts numeric paths (`schema.py:277-304`).

### 6.2 Rejected

| Option | Why it is rejected |
|---|---|
| A looping odometer ramp or timeline | Distance decreases at every loop boundary. **Rejected by the owner, and here** |
| A non-looping `ramp` at the cycle's average speed (the analysis's interim idea) | It never decreases, but it rises while the car is stopped and stops rising at `over`. It is not a real source, so it would still be misleading |
| An accumulator in the runner (`odometer += speed × Δt`) | It adds runner state. The result depends on the tick and request history, not on `t`. That breaks "idempotent for fixed `t`" and the pure-function rule (`runner.py:8-22`, `0006:285-297`) |

### 6.3 Option O1: a derived `distance` generator (the smallest correct source)

```yaml
- path: vehicle.odometer
  type: distance
  of: vehicle.speed      # must be a timeline in the same scenario
  from: 12000            # km at t = 0
```

- **Value:** `from + D(t) / 3600` km, where `D(t)` is the integral of the `of` timeline's
  generator value, in km/h, over `[0, t]`.
- **Closed form for a repeating linear timeline:**
  - `(k, tc) = divmod(t, repeat)`;
  - `D(t) = k × A + P(tc)`;
  - `A` is the per-cycle area, the sum of the trapezoids (precomputed);
  - `P(tc)` is the cumulative trapezoids up to the segment containing `tc`, plus a partial
    trapezoid.
  - For the demo, `A = 640 + 3120 + 600 = 4360` km·s/h, which is **1.211111 km per cycle**.
- **Without `repeat`:** the area up to the last point, plus `vn × (t − an)` while the last
  value is held.
- **With `interpolate: step`:** rectangles instead of trapezoids.
- **It never decreases, and it is continuous at the boundary:**
  - validation requires every `of` point value to be `>= 0` (proposed message:
    `of: distance needs a speed that is never negative, but point 3 of vehicle.speed is -5.0`);
  - at `t = k × repeat`, `P = 0` and `D = k × A`, which equals the left limit.
- **Exact arithmetic.** It is computed in `fractions.Fraction`: the double `t` converts
  exactly, `divmod` is exact, and there is one rounding at the end. That rounding is
  monotone, so a float can never undo a boundary. A float-only
  `k × A + P(tc)` could, in principle, sit one ulp below `(k+1) × A`.
  - Checked in-process on a 0.001 s grid from 0 to 400 s: monotonic
    (Appendix A.1).
  - Cost: about 14.5 µs per call with precomputed areas, against about 0.8 µs for a linear
    timeline (Appendix A.4). It is on the request path of scenario profiles only. That
    needs measuring before it is accepted, given the open latency `STOP`.
- **Resolution.** The runner truncates it into the `int` field (`runner.py:151`), so it
  steps by whole kilometres: 12000 → 12001 at 58 s, when the first cycle's area reaches 3600 km·s/h, and 12048 km at 1 h. `int()` is
  non-decreasing, so truncation keeps the order.
- It integrates the generator's speed, not the truncated stored speed. That is the
  physically right quantity, and it keeps the odometer a function of `t` alone.
- **What it changes in the design:**
  - it is a **seventh generator** (reversing `0006:523`);
  - a generator then references another generator's *configuration*, though never state;
  - `of` is resolved at profile validation, and must name a `timeline` in the same
    scenario;
  - for the smallest version, `of` is restricted to `vehicle.speed`, so the km/h → km
    units are fixed.

  All of this needs its own decision record.

### 6.4 Option O2: no odometer in the demo

- It needs no code and no decision beyond this one.
- The odometer stays 0 in state, and the GUI must mark it unavailable (§7).
- A tester loses nothing, because no PID reports it.

### 6.5 Recommendation

- **Ship the demo with O2.**
- Keep O1 as the specified real source. It becomes a separate decision once the timeline
  extension has landed, if the owner wants distance on screen.
- When a profile drives `vehicle.odometer`, the §7 availability rule marks it available
  automatically.

### 6.6 Would any OBD PID report it? No, not in this design

- **0xA6 (odometer)** is not implemented. Its encoding needs a project evidence entry
  first, under the Phase 5 evidence bar (`docs/decisions/0003-phase-5-obd-evidence.md`,
  `pids.py:13-25`). **No encoding is asserted here.** Adding it would be one table row
  plus that evidence. The masks would extend the chain on their own (`pids.py:10-11`), and
  that changes the `01 A0` bytes, so it is a wire change with its own review.
- **0x31 (distance since codes cleared)** is not a scenario feature. It must reset when
  Mode 04 or UDS 0x14 clears the codes, which couples it to `DtcStore` and makes it not a
  pure function of `t`. It is out of scope.

## 7. GUI: the odometer as "unavailable"

### 7.1 Facts

- `/vehicle` returns `signals: dict(runtime.vehicle.signals)`, which is every field of
  every component (`observe/snapshots.py:15-21`, `vehicle/state.py:150-156`). WS `state`
  embeds the same dict (`snapshots.py:51-52`).
- 0010 §5 defines `signals` as `{dotted path → value}` (`0010:399`).
- The GUI lists every path it receives (`api/static/app.js:458-495`). It shows
  `fmtValue(raw)` (`:502-503`), where `fmtValue(null)` would print `"null"` (`:109-112`).
  - The units map has **no** `vehicle.odometer` entry (`:41-46`), so today the row reads
    `odometer | 0 |` with no unit, not "0 km". It is still misleading, because 0 reads as
    a measurement.
- For an ICE vehicle, `vehicle.odometer` is the only signal a profile cannot set. Every
  other `CommonState` and `IceState` field has a `VehicleConfig` or `EngineConfig` field
  (`schema.py:61-92`, `state.py:18-52`). HEV and BEV also carry unconfigurable
  `battery.current`, `motor.*` and `charging.*` (`state.py:55-80`, `schema.py:78-81`).
  No PID reads any of them.

### 7.2 Options

| | (a) `odometer: int \| None = None`; `/vehicle` sends `null` | (b) the snapshot omits unsourced signals | (c) an explicit availability list |
|---|---|---|---|
| API (0010 §5) | A value can now be `null`, which is a type change inside `signals`. Needs a 0010 amendment | A path disappears from `signals`. The meaning of `signals` changes to "sourced signals" | **Additive:** `/vehicle` and WS `state` gain `unavailable: ["vehicle.odometer"]`, the paths with no source. `signals` is unchanged. Needs a 0010 §5 amendment |
| V1.0 code on `modernization` | **Yes.** `state.py:27`. Also, `signal_types` takes `type(getattr(...))` (`state.py:133`), which would become `NoneType`. The schema would then refuse any scenario that drives the odometer as "not a numeric signal" (`schema.py:297-298`), and the runner's integer check would see `None` (`runner.py:107-110`). So `signal_types` must read annotations instead | None | None. It is computed in `observe/` from `runtime.config.profile`: the configurable fields and the driven scenario paths |
| GUI | Must special-case `null` (today it prints `"null"`) | The row vanishes silently, which hides that the model has the field | Renders `—` with an "unavailable, no source" style and a tooltip: "no source: not settable in a profile and not driven by the scenario" |
| Existing tests | `test_vehicle_state.py:126` asserts `odometer == 0`, and changes. The schema and runner tests need the annotation change | `test_snapshots.py` still passes (it checks serialisation and non-mutation, `:20-48`). Mockup data changes | None break. New assertions are added |
| Branch and records | `modernization` (V1.0 code) and `gui`; 0006 or 0002 (the state model), and 0010 | `gui`; 0010 | **`gui` only; 0010 §5 amendment** |

### 7.3 Recommendation: (c)

- It is the only option that changes no V1.0 code. It fits the `gui` branch rule
  (`0010:14-18`) and keeps the API additive.
- `unavailable` is computed **once per runtime**, not per message. It is static for a run,
  so it adds no publisher work on the hot path (relevant while the latency `STOP` is
  open).
- The rule: a path is unavailable when **no profile field can set it and no scenario
  generator drives it**.
  - With O2 (§6.4), `vehicle.odometer` is unavailable on every shipped profile.
  - With O1 (§6.3), the demo drives it, so it becomes available, with no GUI change.
- The state still holds 0, and `signals` still reports 0. This is honest about the model:
  the API reports state, and the list says that this value has no source.
- Option (a) is the better long-run model: "unknown" should be unrepresentable as a
  number. It is worth proposing on `modernization` after V1.0, with the `signal_types`
  change, but not for this demo.

## 8. Expected OBD bytes (computed)

### 8.1 Method

- The demo's values are not in code yet. Each value is computed from §5.3's points by
  §3.3's linear rule and §3.4's mapping, by a reference evaluator in the session scratchpad
  (Appendix A.1). The coolant uses the existing `ramp` formula verbatim.
- Those values then go through the **real** simulator:
  - a profile built from `ice_scenario.yaml` whose scenario is replaced by `constant`
    generators holding each value;
  - then `parse_profile`, `app.build_runtime(..., clock=SimulatedClock())`, and the real
    `Dispatcher` answering `01 0C 0D 11 04 05` on `0x7E0`.
- So the runner's truncation (`runner.py:151`) and the encoders
  (`pids.py:79-99, 115-132, 160-177, 205-213`) are the shipped code.

### 8.2 Where truncation happens (fact)

There are two truncations in series:

1. **The runner:** `int(value)` for signals whose initial value is an `int`
   (`runner.py:107-110, 151`): `vehicle.speed`, `engine.rpm`. Throttle, load and coolant
   are floats and are stored unchanged.
2. **The encoder:** `_byte` and `_word` apply `int()` and clamp (`pids.py:79-84`):
   - 0x0C is `_word(rpm × 4)`;
   - 0x0D is `_byte(speed)`;
   - 0x11 and 0x04 are `_byte(% × 255 / 100)`;
   - 0x05 is `_byte(°C + 40)`.

For example, 52.5 km/h is stored as 52 (`0D 34`), and a coolant of 23.5 °C encodes as
63 (`05 3F`).

### 8.3 Values and replies at representative times

Request `01 0C 0D 11 04 05`. The replies are payloads as the dispatcher returns them.

| Label | t (s) | tc (s) | rpm | Speed (km/h) | Throttle | Load | Coolant (°C) | Stored rpm, speed | Reply |
|---|---|---|---|---|---|---|---|---|---|
| idle | 2.0 | 2.0 | 800 | 0 | 0 | 20 | 20.583 | 800, 0 | `41 0C 0C 80 0D 00 11 00 04 33 05 3C` |
| mid-acceleration, 2nd gear | 12.0 | 12.0 | 1960 | 35 | 45 | 75 | 23.500 | 1960, 35 | `41 0C 1E A0 0D 23 11 72 04 BF 05 3F` |
| upshift begins | 15.0 | 15.0 | 2800 | 50 | 45 | 75 | 24.375 | 2800, 50 | `41 0C 2B C0 0D 32 11 72 04 BF 05 40` |
| mid-upshift | 15.5 | 15.5 | 2225 | 52.5 | 45 | 75 | 24.521 | 2225, **52** | `41 0C 22 C4 0D 34 11 72 04 BF 05 40` |
| upshift ends, top gear | 16.0 | 16.0 | 1650 | 55 | 45 | 75 | 24.667 | 1650, 55 | `41 0C 19 C8 0D 37 11 72 04 BF 05 40` |
| cruise | 40.0 | 40.0 | 2400 | 80 | 18 | 35 | 31.667 | 2400, 80 | `41 0C 25 80 0D 50 11 2D 04 59 05 47` |
| mid-brake, engine braking | 65.0 | 65.0 | 1600 | 53.333 | 0 | 10 | 38.958 | 1600, **53** | `41 0C 19 00 0D 35 11 00 04 19 05 4E` |
| just before the boundary | 89.999 | 89.999 | 800 | 0 | 0 | 20 | 46.250 | 800, 0 | `41 0C 0C 80 0D 00 11 00 04 33 05 56` |
| one ulp before the boundary | 89.99999999999999 | 89.99999999999999 | 800 | 0 | 0 | 20 | 46.250 | 800, 0 | `41 0C 0C 80 0D 00 11 00 04 33 05 56` |
| **exactly at the boundary** | 90.0 | **0.0** | 800 | 0 | 0 | 20 | 46.250 | 800, 0 | `41 0C 0C 80 0D 00 11 00 04 33 05 56` |
| just after the boundary | 90.001 | 0.0010000000000047748 | 800 | 0 | 0 | 20 | 46.250 | 800, 0 | `41 0C 0C 80 0D 00 11 00 04 33 05 56` |
| 2nd cycle, mid-acceleration | 102.0 | 12.0 | 1960 | 35 | 45 | 75 | **49.750** | 1960, 35 | `41 0C 1E A0 0D 23 11 72 04 BF 05 59` |
| 2nd cycle, cruise | 130.0 | 40.0 | 2400 | 80 | 18 | 35 | **57.917** | 2400, 80 | `41 0C 25 80 0D 50 11 2D 04 59 05 61` |
| 2nd boundary | 180.0 | 0.0 | 800 | 0 | 0 | 20 | 72.500 | 800, 0 | `41 0C 0C 80 0D 00 11 00 04 33 05 70` |
| warm, 4th cycle, cruise | 310.0 | 40.0 | 2400 | 80 | 18 | 35 | 90.000 | 2400, 80 | `41 0C 25 80 0D 50 11 2D 04 59 05 82` |

Read with the table:

- **The loop boundary is continuous.** The cycle ends in the idle segment it begins
  with, so all four cycle-driven PIDs are unchanged across 89.999 → 90.0 → 90.001.
- **The coolant does not reset.** At the same cycle time, t=12 and t=102 give `05 3F` and
  `05 59`, and t=40 and t=130 give `05 47` and `05 61`. The coolant holds `05 82` (90 °C)
  from 240 s.
- **The cycle bytes repeat** at exactly representable times: t=12 and 102, 40 and 130,
  0 and 90 and 180.
- **Odometer (O1 only).** Appendix A.1 also records the odometer O1 would produce: 12000.034 km at
  12 s, 12001.211 km at 90 s, 12002.422 km at 180 s, and 12004.233 km at 310 s
  (Appendix A.1). It is shown in the GUI only, and never on the wire.
- **The truncation hazard, shown (§3.6; Appendix A.2):**

| t | tc | rpm (generator) | Stored rpm | 0x0C bytes |
|---|---|---|---|---|
| 15.3 | 15.3 | 2454.999999999999 | 2454 | `26 58` |
| 105.3 | 15.299999999999997 | 2455.000000000003 | 2455 | `26 5C` |
| 61.0 | 61.0 | 2240.0 (speed 74.66666666666667, stored **74**, `0D 4A`) | 2240 | `23 00` |

### 8.4 Framing (inferred from configuration, not computed)

- `obd_physical` pads with `0x00` (`ice_scenario.yaml:158-164`), which the demo keeps.
- The 12-byte reply needs an ISO-TP first frame and one consecutive frame, as the
  analysis noted for the same request. This design does not claim any wire capture.

## 9. Open questions for the owner

1. **Branch and gate:** A, B or C from §2, and whether that is a 0006 amendment or a new
   decision record (`0011-timeline-interpolation-and-repeat`).
2. **The closing-point rule** (§3.4): accept it, or prefer "`repeat` > last `at`" with a
   defined hold-then-jump?
3. **Odometer:** O2 now (recommended), with O1 as a later decision? Or O1 together with the
   extension, knowingly reversing "exactly six generators"?
4. **GUI:** option (c), recommended? And should the list name a reason (for example
   `{"vehicle.odometer": "no_source"}`) instead of bare paths?
5. **The demo's realism:**
   - add a third gear (two more rpm points) instead of the 2nd-to-top skip shift?
   - lift the throttle during the shift?
   - drive MAF and MAP too, so that `01 10` and `01 0B` move with load?
6. **DTC events in the demo:** none (proposed), or reuse P0128 while the engine is cold?
   Events never repeat either way (§4.1).
7. **Cycle length and warm-up:** 90 s and 240 s as proposed?

## 10. Implementation plan outline (only after approval)

Each task is test-first, in its own commit, with no amend.

| # | Task | Files | Tests |
|---|---|---|---|
| 0 | Record the owner's rulings: the branch, and the 0006 amendment or new record | `docs/decisions/0006-…` or `0011-…`; `docs/modernization-plan.md` if the scope moves | — |
| 1 | **Pin** the `ice_scenario.yaml` goldens against unchanged code | `tests/unit/test_scenario_goldens.py` (new) | §4.2 items 1 and 3, green on the unchanged code |
| 2 | Add the `interpolate` and `repeat` fields and the closing-point validation | `scenario/generators.py` (`TimelineSignal` only) | §4.3 validation tests, and `test_scenario_schema.py` additions |
| 3 | Linear and repeat evaluation; the default path left byte for byte as it is | `scenario/generators.py` | §4.3 value tests; §4.2 items 1 to 3 still green |
| 4 | Correct the stale comment | `tests/unit/test_scenario_schema.py:229` | — |
| 5 | The demo profile | `src/ecu_simulator/profiles/ice_drive_cycle.yaml` (new) | new `tests/unit/test_drive_cycle_profile.py`: validates; five driven paths; §8.3 table rows as byte assertions at the exactly representable times; the coolant not reset (t=12 vs 102); rpm = 56 × speed on 9–15 s, and 30 × speed on 16–21 s and 60–70 s, at quarter-second times; throttle ≥ 45 and load ≥ 75 while accelerating; throttle 0 and load ≤ 10 while braking |
| 6 | Direct verification (plan §10) | — | an in-process run of the §8.3 table; a vcan run of the demo with `isotpsend` and `candump` on a vcan host, recorded under `docs/validation/` |
| 7 | Docs | `docs/conformance.md` row wording (`:217`); `README.md` demo line; profile comments | — |
| 8 | (`gui`) The `unavailable` list | `observe/snapshots.py`, `api/static/app.js`, `api/static/*.css`, 0010 §5 amendment | `test_snapshots.py`: `ice_default` → `["vehicle.odometer"]`; a profile driving it → `[]`; present in WS `state` and within `STATE_MAX_BYTES`; `tests/unit/api/test_server_http.py`: the `/vehicle` key; the owner's manual rendering checklist (0010 §7, with no JS test framework) |
| 9 | (Optional, separate decision) the `distance` generator | `scenario/generators.py`, `config/schema.py` (the `of` resolution), new decision record | monotonic across boundaries on a dense grid; exact `k × A` at multiples; refusal of a negative speed point; a latency measurement before acceptance |

---

## Appendix A. Commands and output

All runs used `.venv/bin/python` (Python 3.12.12, Pydantic 2.13.5), in-process, with no
CAN. Scripts are in the session scratchpad, not in the repo.

### A.1 Values, bytes and odometer (`demo_bytes.py`)

```python
"""Expected OBD bytes for the proposed ice_drive_cycle demo (design aid, not repo code)."""

import math
from fractions import Fraction
from pathlib import Path

from ecu_simulator import app
from ecu_simulator.clock import SimulatedClock
from ecu_simulator.config import load_profile
from ecu_simulator.config.schema import parse_profile
from ecu_simulator.transport import DiagnosticRequest

REPEAT = 90.0
SPEED = [(0, 0), (5, 0), (21, 80), (60, 80), (75, 0), (90, 0)]
RPM = [(0, 800), (5, 800), (9, 1120), (15, 2800), (16, 1650), (21, 2400), (60, 2400), (70, 800), (90, 800)]
THROTTLE = [(0, 0), (5, 0), (6, 45), (21, 45), (22, 18), (59, 18), (60, 0), (90, 0)]
LOAD = [(0, 20), (5, 20), (6, 75), (21, 75), (22, 35), (59, 35), (60, 10), (70, 10), (71, 20), (90, 20)]
COOLANT = (20.0, 90.0, 240.0)  # existing `ramp`: from, to, over -- does not repeat


def linear(points, tc):
    """Proposed `interpolate: linear`: hold before the first and after the last point."""
    i = None
    for k, (at, _) in enumerate(points):
        if at > tc:
            break
        i = k
    if i is None:
        return float(points[0][1])
    if i == len(points) - 1:
        return float(points[-1][1])
    (a0, v0), (a1, v1) = points[i], points[i + 1]
    return v0 + ((v1 - v0) * (tc - a0)) / (a1 - a0)  # multiply before divide


def cycle_time(t, repeat):
    """Proposed repeat mapping: t < 0 is treated as 0; math.fmod is exact."""
    return math.fmod(max(t, 0.0), repeat)


def ramp(t, start, to, over):  # the existing RampSignal.value_at, verbatim
    if t <= 0:
        return start
    if t >= over:
        return to
    return start + (to - start) * (t / over)


def area(points, tc):
    """Exact integral of the linear timeline from 0 to tc, in value*seconds (Fractions)."""
    tc = Fraction(tc)
    total = Fraction(0)
    for (a0, v0), (a1, v1) in zip(points, points[1:]):
        a0, a1, v0, v1 = map(Fraction, (a0, a1, v0, v1))
        if tc <= a0:
            break
        end = min(tc, a1)
        v_end = v0 + (v1 - v0) * (end - a0) / (a1 - a0)
        total += (v0 + v_end) / 2 * (end - a0)
    return total


def distance_km(t, start_km=Fraction(12000)):
    """Proposed `distance` generator: start + integral of speed (km/h) dt, exactly."""
    ft = Fraction(max(t, 0.0))
    k, tc = divmod(ft, Fraction(REPEAT))
    per_cycle = area(SPEED, REPEAT)
    return float(start_km + (k * per_cycle + area(SPEED, tc)) / 3600)


BASE = load_profile(Path(app.__file__).parent / "profiles" / "ice_scenario.yaml").model_dump(
    by_alias=True, exclude_unset=True
)


def reply(values):
    data = dict(BASE)
    data["scenario"] = {
        "tick": 0.5,
        "signals": [{"path": p, "type": "constant", "value": v} for p, v in values.items()],
    }
    for ecu in data["ecus"].values():
        ecu["dtc_events"] = []
    runtime = app.build_runtime(app.RuntimeConfig.build(parse_profile(data)), clock=SimulatedClock())
    response = runtime.dispatcher(DiagnosticRequest(bytes.fromhex("010C0D110405"), 0x7E0))
    stored = {p: runtime.vehicle.get(p) for p in values}
    return stored, response.payload.hex(" ").upper()


def at(t):
    tc = cycle_time(t, REPEAT)
    return {
        "engine.rpm": linear(RPM, tc),
        "vehicle.speed": linear(SPEED, tc),
        "engine.throttle": linear(THROTTLE, tc),
        "engine.engine_load": linear(LOAD, tc),
        "engine.coolant_temp": ramp(t, *COOLANT),
    }, tc

# ... TIMES list as in §8.3, one row printed per time, then a monotonicity check of
# distance_km on a 0.001 s grid from 0 to 400 s.
```

```
$ .venv/bin/python scratchpad/demo_bytes.py
cycle distance = 4360.0 km*s/h = 1.211111 km

label                                                  t                  tc |       rpm   speed    thr   load    cool | stored rpm,spd | odo km       | 01 0C 0D 11 04 05 ->
idle                                                 2.0                 2.0 |   800.000   0.000   0.00  20.00  20.583 |   800,  0      | 12000.000000 | 41 0C 0C 80 0D 00 11 00 04 33 05 3C
mid-acceleration, 2nd gear                          12.0                12.0 |  1960.000  35.000  45.00  75.00  23.500 |  1960, 35      | 12000.034028 | 41 0C 1E A0 0D 23 11 72 04 BF 05 3F
upshift begins                                      15.0                15.0 |  2800.000  50.000  45.00  75.00  24.375 |  2800, 50      | 12000.069444 | 41 0C 2B C0 0D 32 11 72 04 BF 05 40
mid-upshift                                         15.5                15.5 |  2225.000  52.500  45.00  75.00  24.521 |  2225, 52      | 12000.076563 | 41 0C 22 C4 0D 34 11 72 04 BF 05 40
upshift ends, top gear                              16.0                16.0 |  1650.000  55.000  45.00  75.00  24.667 |  1650, 55      | 12000.084028 | 41 0C 19 C8 0D 37 11 72 04 BF 05 40
cruise                                              40.0                40.0 |  2400.000  80.000  18.00  35.00  31.667 |  2400, 80      | 12000.600000 | 41 0C 25 80 0D 50 11 2D 04 59 05 47
mid-brake, engine braking                           65.0                65.0 |  1600.000  53.333   0.00  10.00  38.958 |  1600, 53      | 12001.137037 | 41 0C 19 00 0D 35 11 00 04 19 05 4E
just before the boundary (89.999)                 89.999              89.999 |   800.000   0.000   0.00  20.00  46.250 |   800,  0      | 12001.211111 | 41 0C 0C 80 0D 00 11 00 04 33 05 56
one ulp before the boundary            89.99999999999999   89.99999999999999 |   800.000   0.000   0.00  20.00  46.250 |   800,  0      | 12001.211111 | 41 0C 0C 80 0D 00 11 00 04 33 05 56
exactly at the boundary                             90.0                 0.0 |   800.000   0.000   0.00  20.00  46.250 |   800,  0      | 12001.211111 | 41 0C 0C 80 0D 00 11 00 04 33 05 56
just after the boundary (90.001)                  90.001 0.0010000000000047748 |   800.000   0.000   0.00  20.00  46.250 |   800,  0      | 12001.211111 | 41 0C 0C 80 0D 00 11 00 04 33 05 56
2nd cycle, mid-acceleration                        102.0                12.0 |  1960.000  35.000  45.00  75.00  49.750 |  1960, 35      | 12001.245139 | 41 0C 1E A0 0D 23 11 72 04 BF 05 59
2nd cycle, cruise                                  130.0                40.0 |  2400.000  80.000  18.00  35.00  57.917 |  2400, 80      | 12001.811111 | 41 0C 25 80 0D 50 11 2D 04 59 05 61
2nd boundary                                       180.0                 0.0 |   800.000   0.000   0.00  20.00  72.500 |   800,  0      | 12002.422222 | 41 0C 0C 80 0D 00 11 00 04 33 05 70
warm, 4th cycle, cruise                            310.0                40.0 |  2400.000  80.000  18.00  35.00  90.000 |  2400, 80      | 12004.233333 | 41 0C 25 80 0D 50 11 2D 04 59 05 82

odometer never decreases across boundaries (checked on a 0.001 s grid, 0..400 s):
monotonic: True
odometer(0.0) = 12000.000000 km -> stored int 12000
odometer(90.0) = 12001.211111 km -> stored int 12001
odometer(180.0) = 12002.422222 km -> stored int 12002
odometer(900.0) = 12012.111111 km -> stored int 12012
odometer(3600.0) = 12048.444444 km -> stored int 12048
```

### A.2 The truncation hazard, and the step and linear boundary example (`extra.py`, which reuses A.1's functions)

The dictionaries in the first three lines are abridged here with `...`. The last line
prints `math.fmod(0.1*3, 0.3)`, `0.1*3`, `math.fmod(270.0, 90.0)` and
`math.fmod(1e9*90.0, 90.0)`.

```
$ .venv/bin/python scratchpad/extra.py
15.3 15.3 {'engine.rpm': '2454.999999999999', 'vehicle.speed': '51.5', ...} {'engine.rpm': 2454, 'vehicle.speed': 51, ...} 41 0C 26 58 0D 33 11 72 04 BF 05 40
61.0 61.0 {'engine.rpm': '2240.0', 'vehicle.speed': '74.66666666666667', ...} {'engine.rpm': 2240, 'vehicle.speed': 74, ...} 41 0C 23 00 0D 4A 11 00 04 19 05 4D
105.3 15.299999999999997 {'engine.rpm': '2455.000000000003', 'vehicle.speed': '51.499999999999986', ...} {'engine.rpm': 2455, 'vehicle.speed': 51, ...} 41 0C 26 5C 0D 33 11 72 04 BF 05 5A
step 29.999 29.999 10 linear 19.999666666666666
step 30.0 30.0 20 linear 20.0
step 59.999 59.999 20 linear 10.000333333333334
step 59.99999999999999 59.99999999999999 20 linear 10.000000000000002
step 60.0 0.0 10 linear 10.0
step 60.001 0.0009999999999976694 10 linear 10.000333333333332
step 90.0 30.0 20 linear 20.0
step 120.0 0.0 10 linear 10.0
5.551115123125783e-17 0.30000000000000004 0.0 0.0
```

The last line shows that `fmod` is exact on its inputs. `0.1*3` is not `0.3`, so its
remainder is not zero. That is why §3.6 promises determinism in the double `t`, not in
decimal time.

### A.3 Today's refusal of the new keys, and Pydantic's messages

```
$ .venv/bin/python -   # ice_scenario.yaml with three edits, through parse_profile
profile x.yaml is invalid (3 problem(s)):
  scenario.signals.0.timeline.points: timeline points must be in ascending 'at' order, got [0.0, 200.0, 20.0, ...]
  scenario.signals.1.timeline.repeat: Extra inputs are not permitted
  scenario.signals.2.ramp.over: Input should be greater than 0

$ .venv/bin/python -   # Literal["step","linear"] and Field(gt=0) on Pydantic 2.13.5
["Input should be 'step' or 'linear'"]
['Input should be greater than 0']
```

### A.4 Per-call cost (reference code, not the eventual implementation)

```
linear timeline, us/call: 0.818
exact distance, us/call: 14.5   (Fraction arithmetic, areas precomputed)
```
