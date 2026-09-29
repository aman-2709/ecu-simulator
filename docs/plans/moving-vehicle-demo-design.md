# Moving-vehicle demo profile and the smallest timeline extension — design

Status: **revised after the owner's review of 2026-09-29, and stopped for review again.
Nothing is implemented.**
- The owner's decisions are recorded in §2.
- The timeline extension (§5) is **post-V1.0 work** and is not implemented now.
- A sooner path that needs no code change, a profile built only from today's generators,
  is evaluated in §4. It awaits the owner's decision.
- This file is the only change. No `src/`, `tests/` or profile file was touched.

Written on branch `gui` (worktree `.claude/worktrees/gui`).
- Every "fact" was re-verified against the code on this branch, with file:line.
- Everything under a "Proposal" heading is a proposal, not a fact about the code.
- All OBD bytes were computed in-process by the simulator's own loader, schema,
  generators, runner, dispatcher and encoders, with no CAN (Appendices A and B).

## 1. Scope and status

- **Asked for:**
  - a separate moving-vehicle demo profile;
  - the smallest timeline extension that makes it readable (`interpolate`, `repeat`);
  - an odometer that never decreases, or none at all;
  - a way to show an unsourced odometer as unavailable in the GUI;
  - since the review:
    - an evaluation of a profile that uses only today's generators;
    - nonfinite-input validation;
    - a restated periodicity requirement.
- **Not asked for, and not proposed:**
  - a looping odometer ramp;
  - any change to `ice_default.yaml` or `ice_scenario.yaml`;
  - any new OBD PID;
  - conditions or triggers;
  - randomness.
- **Stop point.** The design stops for review again. No scenario, timeline, profile or GUI
  code is written until the owner decides the open questions in §11.
- **Unaffected and still open:**
  - The M2 early-check latency `STOP` stays open and is not accepted
    (`docs/decisions/0010-gui-observer-api.md:8-10`,
    `docs/validation/gui-m2-early-check.md`). Nothing here measures or changes it.
  - The hosted-CI `CAN_ISOTP` skips stay as they are. GitHub-hosted runners have no
    `can_isotp` (`docs/modernization-plan.md:344-347`), and decision 0009 proposes the
    runner that would fix that. Every vcan check proposed here runs only on a vcan host.

## 2. Decisions (owner, 2026-09-29)

The owner's words:

> "Approve the closing-point rule and O2 (no demo odometer), but do not implement the
> timeline extension yet. Treat it as post-V1.0 work; option A expands V1.0 and option C
> conflicts with the gui branch rule. Evaluate whether a separate profile using today's
> repeating stepped generator can show a moving vehicle sooner. Add validation for
> nonfinite repeat/point inputs. For periodicity, require deterministic results at a
> given float time and correct boundary behavior; document that arbitrary decimal times
> one cycle apart need not give byte-identical RPM. Keep the optional distance generator
> and PIDs for a later decision. Revise the design and stop for review."

| Topic | Decision | Where it applies |
|---|---|---|
| The closing-point rule for `repeat` | **Approved** | §5.4 |
| O2: no odometer in the demo | **Approved** | §8.4, and the stepped profile in §4 |
| The timeline extension | **Post-V1.0. Not implemented now** | §3, §5 |
| Option A (land on `modernization` now) | **Rejected**, because it expands V1.0 | §3 |
| Option C (land on `gui` only) | **Rejected**, because it conflicts with the `gui` branch rule | §3 |
| Where the extension lands post-V1.0 | **Decided post-V1.0.** The owner has not chosen a branch or phase, and this design does not claim one | §3 |
| A profile using today's `stepped` generator | **To be evaluated** (§4) | §4 |
| Nonfinite `repeat` and point inputs | **Must be rejected** by the extension | §5.8 |
| Periodicity | **Required:** deterministic at a given float time, and correct at the boundaries. **Documented, not required:** decimal times one cycle apart need not give byte-identical rpm | §5.6, §6.3 |
| The `distance` generator (O1), PIDs 0xA6 and 0x31 | **Deferred** to a later decision | §8.3, §8.5 |

## 3. Where this work sits (facts)

- The scenario engine is V1.0 code. Phase 7 is listed under V1.0
  (`docs/modernization-plan.md:123`, `:249`).
  - It was approved with rulings in 0006. One ruling is **"Exactly the six generators,
    pure functions of `t`"** (`docs/decisions/0006-phase-7-scenario-and-testerpresent.md:523`).
  - 0006 §5.1 defines the timeline as step-and-hold (`0006:310`), and calls it "absolute"
    in contrast to `stepped`, which repeats (`0006:312`).
  - The conformance row reads "Six deterministic generators as pure functions of elapsed
    time" (`docs/conformance.md:217`).
- V1.0 is not tagged without Phase 8b's evidence (`docs/modernization-plan.md:364-365`).
- Branch `gui` starts from `modernization` at `a57b98f`, and it is **not merged into
  `modernization` until V1.0 is tagged**. Nothing on `gui` changes V1.0 scope, the 8b gate
  or any conformance status (`0010:14-18`, `docs/modernization-plan.md:567-577`).
- The standing gates apply to any change: the phase completion gate
  (`docs/modernization-plan.md:694-756`) and the documentation and standards verification
  gate (`:758-`).

What follows from the decisions:

- `interpolate` and `repeat` change the semantics of a V1.0 generator. They therefore
  amend 0006 §5.1 and §5.3, in a decision record written when the post-V1.0 work starts.
- **Option A** would have landed that on `modernization` before 8b, which expands V1.0.
  The owner rejected it for that reason.
- **Option C** would have changed V1.0 code on `gui`, which the `gui` rule forbids
  (`0010:14-18`). The owner rejected it for that reason.
- The landing (which branch, which phase) is **decided post-V1.0**.
- §5 to §7 and §10 are kept as the approved-in-part specification for that later work.
- §4 is the only path that could show a moving vehicle before then. §9's `unavailable`
  list touches only `gui` files and 0010, and it does not depend on the extension.

## 4. The sooner path: a profile using only today's generators

### 4.1 Facts about `stepped`

- **Semantics.** `value_at(t) = values[step % len(values)]`, with
  `step = int(t // interval) if t > 0 else 0` (`scenario/generators.py:96-105`,
  `0006:308`).
- **Step length.** There is one `interval` for every step. A per-step length is **not**
  configurable (`generators.py:101`), so the cycle length is `len(values) × interval`.
- **Wrap.** Just before `t = k × len × interval` the value is `values[-1]`. **At** that
  instant it is `values[0]`, because `step % len == 0`. The wrap is a plain step from the
  last value to the first. `t <= 0` gives `values[0]` (`generators.py:104`).
- **Float behaviour.** With `interval: 1`, `t // 1.0` is the exact floor of the double
  `t`. The same float `t` always gives the same step.
  - `15.3` and `105.3` land on steps 15 and 105, so they agree (Appendix B).
  - Two decimal times one cycle apart could disagree only within an ulp of a step edge,
    where `t + 90` rounds across a whole second.
- **Schema limits.**
  - `values` must be non-empty (`Field(min_length=1)`), with **no maximum length**
    (`generators.py:100`).
  - `interval` must be `> 0` (`generators.py:101`).
  - `ScenarioConfig.signals` has no length limit either (`config/schema.py:195`).
- **Nonfinite values are not rejected** (finding, §5.8.2): `values: [.inf]` and
  `interval: .inf` both validate today (Appendix C).
- **Combining with a non-repeating `ramp`.**
  - Each generator is evaluated independently at the same `t` (`runner.py:148-151`).
  - A `ramp` is a function of absolute `t`: it holds `to` after `over`
    (`generators.py:68-73`). It ignores the stepped cycle, so the coolant keeps rising
    through the loops and never resets.
- **Phase alignment is not validated.** Each `stepped` wraps at its own
  `len × interval`. Nothing checks that the four lists share a length. A list one value
  short would drift out of phase with the others silently, a second per lap.

### 4.2 Proposal: the profile

- **The same 90 s cycle as §7.** `interval: 1`, **90 values per signal** for speed, rpm,
  throttle and load.
- **How the values were derived.** Each value is the §7 knot line sampled at the start of
  its second. So each 1 s step holds the value the linear timeline would have at that
  whole second.
- **Speed** is written as the whole km/h the runner stores anyway. The runner truncates
  integer signals (`runner.py:107-110, 151`), so `74.67` and `74` give the same byte, and
  the file shows what the wire carries.
- **rpm stays coherent with speed:**
  - exactly 56 × speed on steps 9–15 (2nd gear);
  - exactly 30 × speed on steps 16–21 (top gear);
  - within about 1% on steps 61–69, because speed is written truncated and rpm is not.
- **Coolant** is the existing `ramp` from 20 to 90 °C over 240 s. It does not repeat.
- **No odometer**, per O2. `vehicle.odometer` stays undriven.
- **No `dtc_events`** (open question 6).
- **`tick: 0.5`**, as in `ice_scenario.yaml`.
- The `vehicle` and `ecus` blocks are as in `ice_scenario.yaml`
  (`ice_scenario.yaml:30-52, 121-170`).
- The scenario part below was generated by Appendix B's script and loaded through the
  real loader and schema. It validates: four `stepped` signals of 90 values each, and one
  `ramp`.

```yaml
scenario:
  tick: 0.5
  signals:
    - path: vehicle.speed
      type: stepped
      interval: 1
      values: [
          0, 0, 0, 0, 0, 0, 5, 10, 15, 20,  # 0-9 s
          25, 30, 35, 40, 45, 50, 55, 60, 65, 70,  # 10-19 s
          75, 80, 80, 80, 80, 80, 80, 80, 80, 80,  # 20-29 s
          80, 80, 80, 80, 80, 80, 80, 80, 80, 80,  # 30-39 s
          80, 80, 80, 80, 80, 80, 80, 80, 80, 80,  # 40-49 s
          80, 80, 80, 80, 80, 80, 80, 80, 80, 80,  # 50-59 s
          80, 74, 69, 64, 58, 53, 48, 42, 37, 32,  # 60-69 s
          26, 21, 16, 10, 5, 0, 0, 0, 0, 0,  # 70-79 s
          0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  # 80-89 s
        ]
    - path: engine.rpm
      type: stepped
      interval: 1
      values: [
          800, 800, 800, 800, 800, 800, 880, 960, 1040, 1120,  # 0-9 s
          1400, 1680, 1960, 2240, 2520, 2800, 1650, 1800, 1950, 2100,  # 10-19 s
          2250, 2400, 2400, 2400, 2400, 2400, 2400, 2400, 2400, 2400,  # 20-29 s
          2400, 2400, 2400, 2400, 2400, 2400, 2400, 2400, 2400, 2400,  # 30-39 s
          2400, 2400, 2400, 2400, 2400, 2400, 2400, 2400, 2400, 2400,  # 40-49 s
          2400, 2400, 2400, 2400, 2400, 2400, 2400, 2400, 2400, 2400,  # 50-59 s
          2400, 2240, 2080, 1920, 1760, 1600, 1440, 1280, 1120, 960,  # 60-69 s
          800, 800, 800, 800, 800, 800, 800, 800, 800, 800,  # 70-79 s
          800, 800, 800, 800, 800, 800, 800, 800, 800, 800,  # 80-89 s
        ]
    - path: engine.throttle
      type: stepped
      interval: 1
      values: [
          0, 0, 0, 0, 0, 0, 45, 45, 45, 45,  # 0-9 s
          45, 45, 45, 45, 45, 45, 45, 45, 45, 45,  # 10-19 s
          45, 45, 18, 18, 18, 18, 18, 18, 18, 18,  # 20-29 s
          18, 18, 18, 18, 18, 18, 18, 18, 18, 18,  # 30-39 s
          18, 18, 18, 18, 18, 18, 18, 18, 18, 18,  # 40-49 s
          18, 18, 18, 18, 18, 18, 18, 18, 18, 18,  # 50-59 s
          0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  # 60-69 s
          0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  # 70-79 s
          0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  # 80-89 s
        ]
    - path: engine.engine_load
      type: stepped
      interval: 1
      values: [
          20, 20, 20, 20, 20, 20, 75, 75, 75, 75,  # 0-9 s
          75, 75, 75, 75, 75, 75, 75, 75, 75, 75,  # 10-19 s
          75, 75, 35, 35, 35, 35, 35, 35, 35, 35,  # 20-29 s
          35, 35, 35, 35, 35, 35, 35, 35, 35, 35,  # 30-39 s
          35, 35, 35, 35, 35, 35, 35, 35, 35, 35,  # 40-49 s
          35, 35, 35, 35, 35, 35, 35, 35, 35, 35,  # 50-59 s
          10, 10, 10, 10, 10, 10, 10, 10, 10, 10,  # 60-69 s
          10, 20, 20, 20, 20, 20, 20, 20, 20, 20,  # 70-79 s
          20, 20, 20, 20, 20, 20, 20, 20, 20, 20,  # 80-89 s
        ]
    # Warm-up happens once: a ramp of absolute time, not part of the cycle.
    - path: engine.coolant_temp
      type: ramp
      from: 20
      to: 90
      over: 240
```

**The loop boundary** (`generators.py:103-105`):

| t | Step | Every cycle signal |
|---|---|---|
| `89.999`, and one ulp below 90 | 89 | `values[89]`: idle |
| `90.0` exactly | 0 | `values[0]`: idle |
| `90.001` | 0 | `values[0]`: idle |

- Every list ends on the value it starts with. So the wrap is a step from idle to idle,
  and nothing on the wire changes.
- The coolant keeps rising across the boundary.

### 4.3 Expected bytes (computed through the real generators)

Method (Appendix B):
- the profile file above was written;
- it was read by `load_profile` (the real ruamel loader and schema);
- it was built with `app.build_runtime(..., clock=SimulatedClock())`;
- the clock was advanced to `t`, and the real `Dispatcher` asked `01 0C 0D 11 04 05` on
  `0x7E0`.

| Label | t (s) | Step | Stored rpm, speed, throttle, load | Coolant (°C) | Reply |
|---|---|---|---|---|---|
| idle | 2.0 | 2 | 800, 0, 0.0, 20.0 | 20.583 | `41 0C 0C 80 0D 00 11 00 04 33 05 3C` |
| mid-acceleration, 2nd gear | 12.0 | 12 | 1960, 35, 45.0, 75.0 | 23.500 | `41 0C 1E A0 0D 23 11 72 04 BF 05 3F` |
| same step, 0.5 s later | 12.5 | 12 | 1960, 35, 45.0, 75.0 | 23.646 | `41 0C 1E A0 0D 23 11 72 04 BF 05 3F` |
| upshift begins | 15.0 | 15 | 2800, 50, 45.0, 75.0 | 24.375 | `41 0C 2B C0 0D 32 11 72 04 BF 05 40` |
| t = 15.3 | 15.3 | 15 | 2800, 50, 45.0, 75.0 | 24.462 | `41 0C 2B C0 0D 32 11 72 04 BF 05 40` |
| upshift ends, top gear | 16.0 | 16 | 1650, 55, 45.0, 75.0 | 24.667 | `41 0C 19 C8 0D 37 11 72 04 BF 05 40` |
| cruise | 40.0 | 40 | 2400, 80, 18.0, 35.0 | 31.667 | `41 0C 25 80 0D 50 11 2D 04 59 05 47` |
| brake, first step | 61.0 | 61 | 2240, 74, 0.0, 10.0 | 37.792 | `41 0C 23 00 0D 4A 11 00 04 19 05 4D` |
| mid-brake | 65.0 | 65 | 1600, 53, 0.0, 10.0 | 38.958 | `41 0C 19 00 0D 35 11 00 04 19 05 4E` |
| **just before the boundary** | 89.999 | 89 | 800, 0, 0.0, 20.0 | 46.250 | `41 0C 0C 80 0D 00 11 00 04 33 05 56` |
| **one ulp before the boundary** | 89.99999999999999 | 89 | 800, 0, 0.0, 20.0 | 46.250 | `41 0C 0C 80 0D 00 11 00 04 33 05 56` |
| **exactly at the boundary** | 90.0 | 0 | 800, 0, 0.0, 20.0 | 46.250 | `41 0C 0C 80 0D 00 11 00 04 33 05 56` |
| **just after the boundary** | 90.001 | 0 | 800, 0, 0.0, 20.0 | 46.250 | `41 0C 0C 80 0D 00 11 00 04 33 05 56` |
| 2nd cycle, mid-acceleration | 102.0 | 12 | 1960, 35, 45.0, 75.0 | **49.750** | `41 0C 1E A0 0D 23 11 72 04 BF 05 59` |
| 2nd cycle, t = 105.3 | 105.3 | 15 | 2800, 50, 45.0, 75.0 | **50.712** | `41 0C 2B C0 0D 32 11 72 04 BF 05 5A` |
| 2nd cycle, cruise | 130.0 | 40 | 2400, 80, 18.0, 35.0 | **57.917** | `41 0C 25 80 0D 50 11 2D 04 59 05 61` |
| 2nd boundary | 180.0 | 0 | 800, 0, 0.0, 20.0 | 72.500 | `41 0C 0C 80 0D 00 11 00 04 33 05 70` |
| warm, 4th cycle, cruise | 310.0 | 40 | 2400, 80, 18.0, 35.0 | 90.000 | `41 0C 25 80 0D 50 11 2D 04 59 05 82` |

Read with the table:

- **At the whole-second steps, every reply equals §10.3's linear-timeline reply**,
  because the values are the same samples. The two differ only between whole seconds:
  - at t=15.5, stepped holds 2800 rpm (`0C 2B C0`), and linear gives 2225 (`0C 22 C4`);
  - at t=15.3, stepped gives 2800 rpm, and linear gives 2454.
- **The coolant keeps rising across the loops:** `05 3F` at t=12, `05 59` at t=102.
- **The cycle PIDs agree at t=15.3 and t=105.3** (`0C 2B C0`). Here, unlike the linear
  timeline (§5.6), the step holds for a whole second.

### 4.4 Trade-offs against the extension

| | Stepped profile, now | Linear `timeline` with `repeat`, post-V1.0 |
|---|---|---|
| Code change | **None.** Only V1.0 generators (0006 §5.1) | Amends a V1.0 generator; post-V1.0 |
| Shape | A 1 s staircase: speed moves in 5 km/h steps; the upshift is one step, 2800 → 1650 rpm | Smooth ramps between knots |
| File | About 66 lines of scenario: 360 numbers, with the knots invisible. Changing a phase means re-deriving many values, which is why the generating script matters | About 50 lines: 33 knots, each readable as intent |
| Silent-error risk | **Lists of unequal length drift out of phase with no error** (§4.1). A test must pin every length to 90 | The closing-point rule validates every signal's cycle |
| Values between whole seconds | The value at the start of the second, held | Interpolated. Byte periodicity at decimal times is not guaranteed (§5.6) |
| In the GUI | See below | See below |

In the GUI:

- `state` is pushed at most every 0.25 s, and only when it changed
  (`observe/limits.py:16`, `observe/publisher.py:265-272`).
- Snapshots never apply the scenario (`observe/snapshots.py:1-3`). So a value on screen
  moves only when the tick (`tick: 0.5`) or a request applies it.
- In the M3a signals table, which shows numbers and not plots yet (0010 §7), the stepped
  profile changes speed and rpm **once a second**. The linear timeline would change them
  about **twice a second**, at the tick. Both read as a moving vehicle in a number table.
- The staircase becomes visible only as steps on a plot, which is M3b's sparklines. At a
  90 s scale, 1 s steps are small.

**Assessment.** The stepped profile can show a moving vehicle **now**, with no code change,
at the cost of a long list and a 1 s staircase. It is a stopgap: when the extension lands
post-V1.0, the §7 profile replaces it with the same cycle and the same bytes at whole
seconds.

A coarser step does not read better. `interval: 2` halves the lists to 45 values, but
speed then jumps 10 km/h at a time and the upshift lasts 2 s.

### 4.5 Where the file would live

Facts:

- **Shipped profiles are package data.** The wheel includes
  `src/ecu_simulator/profiles/*.yaml` (`pyproject.toml:63`).
- **`--profile` accepts any path.**
  - `--profile PATH` (`cli.py:59-63`) becomes `Path(args.profile)` (`cli.py:78`).
  - `load_profile` reads and validates any file (`config/__init__.py:19-21`,
    `config/loader.py:29-48`).
  - The M3a live demo already runs with an explicit `--profile <path>`
    (`docs/validation/gui-m3a-live-demo.md:49`).
- **The `gui` rules:**
  - `gui` is not merged until V1.0 is tagged, and nothing on it changes V1.0 scope
    (`0010:14-18`, `docs/modernization-plan.md:567-577`).
  - M1's plan listed "the profiles" among files it must not modify
    (`docs/plans/gui-m1-implementation.md:60-62`). That was a constraint on M1, but it
    shows the intent.
  - M2's plan says "Nothing in this plan touches `modernization`"
    (`docs/plans/gui-m2-implementation.md:95-96`).
  - There is a precedent for GUI-track material outside package data:
    `docs/mockups/m3a-dashboard/`, "not package data"
    (`docs/plans/gui-m2-implementation.md:2771`).
- There is no `examples/` directory today (repository root listing).

Options, not decided:

| | Location | Branch | V1.0 content? | Notes |
|---|---|---|---|---|
| L1 | `src/ecu_simulator/profiles/ice_drive_cycle_stepped.yaml` | `gui` | **Yes, in effect.** It is package data in a V1.0 directory, which the `gui` rule's intent excludes | Tests can find it next to `ice_scenario.yaml` |
| L2 | The same path | `modernization` | **Yes.** It adds a shipped profile before 8b, which is what the owner rejected A for, though with no code | Needs an owner ruling and a conformance note |
| L3 | **`docs/examples/ice_drive_cycle_stepped.yaml`** | `gui` | **No.** It is not package data, and it is run with `--profile docs/examples/ice_drive_cycle_stepped.yaml` | Matches the `docs/mockups/` precedent. Tests on `gui` load it by repository path |
| L4 | `examples/ice_drive_cycle_stepped.yaml` (a new top-level directory) | `gui` | No | The same as L3, but it adds a new top-level directory |

**Recommendation: L3.** It is the only option that is neither V1.0 content nor a new
top-level convention. It keeps the GUI demo on the GUI track, and it can move into
`profiles/` when the extension lands post-V1.0.

## 5. The timeline extension (specification for post-V1.0 work, not implemented now)

### 5.1 Today (fact)

- `TimelineSignal` has `type` and `points`. Points must ascend by `at`, and **equal `at`
  values are allowed**: the check is `times != sorted(times)`
  (`scenario/generators.py:141-153`).
- `value_at(t)` returns the value of the last point whose `at <= t`, and the first point's
  value before the first point (`generators.py:155-161`). It never interpolates, and it
  holds the last value forever.
- Generator models forbid unknown keys (`generators.py:34`). Today, `repeat:` on a
  timeline is refused as `Extra inputs are not permitted` (Appendix A.3).
- **Time is a Python `float`.**
  - `ScenarioSync.elapsed` is `clock.now() - origin` (`scenario/sync.py:31-32`).
  - The origin is read once, at build (`sync.py:25`, `app.py:222-224`).
  - `MonotonicClock` returns `time.monotonic()` (`clock.py:24-25`).
  - `SimulatedClock.advance` accumulates `self._now += float(seconds)` (`clock.py:37-41`).
- **The runner:**
  - it calls `value_at(t)` for every signal, and writes `int(value)` into integer signals
    (`scenario/runner.py:148-151`);
  - the integer-or-float choice is made once, from the vehicle's initial value types
    (`runner.py:107-110`);
  - a `t` earlier than the last applied one is refused (`runner.py:136-143`).

### 5.2 Proposal: two optional fields on `timeline` only

```yaml
- path: vehicle.speed
  type: timeline
  interpolate: linear   # step | linear; default step
  repeat: 90            # seconds; default absent, meaning no repeat
  points: [...]
```

- `interpolate: Literal["step", "linear"] = "step"`.
- `repeat: float | None = Field(default=None, gt=0, allow_inf_nan=False)`.
- With both absent, or with `interpolate: step` and no `repeat`, `value_at` runs the
  **existing code path unchanged** (§6.2).
- No other generator gains either field.

### 5.3 Linear: the value at `tc`

`tc` is the evaluation time: `t` without `repeat`, or the cycle time (§5.4) with it. The
points are `(a0, v0) … (an, vn)`, ascending.

| Case | Value |
|---|---|
| `tc < a0` | `v0`, held, as step does today (`generators.py:156`) |
| `ai <= tc < ai+1`, `i` the **last** index with `ai <= tc` | `vi + ((vi+1 − vi) × (tc − ai)) / (ai+1 − ai)` |
| `tc >= an` | `vn`, held |
| `tc == ai` exactly | `vi` exactly |
| Two points share one `at` | A zero-width segment is never selected, so there is no division by zero. The value jumps at that `at`, and the later point wins, exactly as step does today |

- **Multiply before divide.** With integer knots, a time whose exact result is a whole
  number gives that whole number exactly, for example 1960 rpm at t=12.
- Only `+ − × ÷` are used, with no libm, unlike `sine` (`generators.py:79-83`,
  `0006:315-321`).

### 5.4 Repeat: the cycle time, and the closing-point rule (approved)

- **Mapping.** `tc = math.fmod(max(t, 0.0), repeat)`.
  - A `t < 0` never happens in the runtime. It is treated as 0, like `ramp` and `stepped`
    (`generators.py:69, 104`).
- **The closing-point rule, approved by the owner on 2026-09-29.** When `repeat` is set:
  1. the first point's `at` is `0`;
  2. the last point's `at` equals `repeat`;
  3. the last point's `value` equals the first point's `value`.
- **Why this rule:**
  - the file states the value at `t = k × repeat`;
  - linear is continuous at the wrap by construction;
  - a jump has to be written, as two points at one `at`, so it is visible;
  - it is `stepped`'s wrap to `values[0]` (`generators.py:103-105`), made explicit.

### 5.5 The loop boundary, exactly

Here `k >= 1`, and `ε` is small: one ulp, or 0.001 s.

| `t` | `tc` | step | linear |
|---|---|---|---|
| `k·repeat − ε` | `repeat − ε` | the value of the point before the closing point | `→ vn = v0` (continuous) |
| `k·repeat` exactly | `0.0` exactly | `v0` | `v0` |
| `k·repeat + ε` | `ε` | `v0` | `v0 + slope0 × ε` |

A worked example, with points `(0,10) (30,20) (60,10)` and `repeat: 60` (Appendix A.2):

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

### 5.6 Periodicity and floating point (restated per the owner's decision)

**Required:**

1. **Determinism at a given float time.** `value_at(t)` is a pure function of the double
   `t`. The same double gives the same value, in two calls, in two instances built from
   the same configuration, and in two processes. On any IEEE-754 platform it gives the same
   bytes, because only correctly rounded basic operations and the exact `fmod` are used.
2. **Correct boundary behaviour.**
   - At an exact multiple `t = k × repeat`, `math.fmod` returns exactly `0.0`, because
     IEEE-754 `fmod` is exact. The value is `v0`: `fmod(270.0, 90.0) == 0.0`,
     `fmod(9e10, 90.0) == 0.0` (Appendix A.2).
   - Just before and just after, the values follow §5.5.

**Documented, not required:** arbitrary decimal times one cycle apart need not give
byte-identical results.

- `105.3 − 90` in doubles is `15.299999999999997`, not `15.3`.
- The linear rpm is then `2455.000000000003` against `2454.999999999999`, and the runner's
  truncation (`runner.py:151`) turns that into **2455 rpm against 2454 rpm** (`0C 26 5C`
  against `0C 26 58`, §10.3).
- This is deterministic, but it is not byte-periodic. The same hazard exists today for
  `ramp` and at every truncation boundary.

Rejected alternatives:

- quantising `tc`, which adds a second rule;
- rational `t`, which would change the clock seam (`runner.py:126`, `sync.py:35`).

### 5.7 Other generators

- `constant`, `ramp`, `sine`, `stepped` and `sequence` are unchanged
  (`generators.py:50-131`).
- The six-generator test stays as written (`tests/unit/test_scenario_generators.py:42-44`).

### 5.8 Validation

#### 5.8.1 Messages, including nonfinite input

- A model validator on `TimelineSignal` prefixes its messages with `repeat: `, so `_join`
  moves the sub-path into the location (`config/schema.py:354-368`).
- Several problems are joined with `; ` (`schema.py:357-360`).
- Nonfinite input is refused by `allow_inf_nan=False` on `repeat`, `TimelinePoint.at` and
  `TimelinePoint.value`, which produces Pydantic's own message. That matches the style of
  the existing field errors, such as `Input should be greater than 0`
  (Appendix A.3, Appendix C).

| Problem | Message as `validate-config` prints it |
|---|---|
| First point not at 0 | `scenario.signals.0.timeline.repeat: a repeating timeline must start its cycle at 0, but its first point is at 5.0` |
| Last point not at `repeat` | `scenario.signals.0.timeline.repeat: a repeating timeline must end with a point at its repeat time 90.0, but its last point is at 75.0` |
| The closing value differs | `scenario.signals.0.timeline.repeat: the last point closes the cycle, so its value must equal the first point's value 0.0, got 5.0` |
| `repeat: 0` or negative | `scenario.signals.0.timeline.repeat: Input should be greater than 0` |
| `repeat: .nan`, `.inf` or `-.inf` | `scenario.signals.0.timeline.repeat: Input should be a finite number` |
| A point's `at` is `.nan`, `.inf` or `-.inf` | `scenario.signals.0.timeline.points.3.at: Input should be a finite number` |
| A point's `value` is `.nan`, `.inf` or `-.inf` | `scenario.signals.0.timeline.points.3.value: Input should be a finite number` |
| `interpolate: cubic` | `scenario.signals.0.timeline.interpolate: Input should be 'step' or 'linear'` |
| `repeat` on another generator | `scenario.signals.1.ramp.repeat: Extra inputs are not permitted` (unchanged) |

- The finite-number messages and their `type` (`finite_number`) were checked on Pydantic
  2.13.5 (Appendix C).
- YAML spells these values `.nan`, `.inf` and `-.inf`, and an overflowing literal such as
  `1e400` also loads as `inf` (Appendix C).
- Finiteness on `TimelinePoint` applies to **step** timelines too, because the class is
  shared. That is deliberate, and it is a tightening. A profile with a nonfinite point
  becomes invalid, and every valid profile is unaffected, which the §6.2 goldens prove.

#### 5.8.2 Finding: the current schema accepts nonfinite values almost everywhere

This is recorded as a finding. No change is proposed here for the existing generators; it
is a later decision (open question 8).

Checked through `parse_profile` (Appendix C):

| Input | Result today | Why |
|---|---|---|
| timeline point `value: nan` | **accepted** | `TimelinePoint.value: float`, no constraint (`generators.py:138`) |
| timeline point `at: inf` | **accepted** | `Field(ge=0)`, and `inf >= 0` (`generators.py:137`) |
| timeline point `at: nan` | refused: `Input should be greater than or equal to 0` | `NaN >= 0` is false, so it is refused by accident, with a misleading message |
| `ramp.over: inf`, `ramp.to: nan` | **accepted** | `generators.py:64-66` |
| `sine.centre: inf` | **accepted**; `sine.period: nan` is refused by `gt=0` | `generators.py:87-89` |
| `stepped.values: [inf]`, `stepped.interval: inf` | **accepted** | `generators.py:100-101` |
| `constant.value: nan` | **accepted** | `generators.py:54` |
| `sequence` step `for: inf` | **accepted** | `generators.py:112` |
| `scenario.tick: inf` | **accepted** | `schema.py:194` |
| DTC event `at: inf` | **accepted** | `Field(ge=0)`, and `inf >= 0` (`scenario/events.py:47`) |
| initial `vehicle.engine.coolant_temp: nan` | **accepted** | `schema.py:65`, an unbounded `float` |

What an accepted nonfinite value does, in-process (Appendix C):

- A NaN point on the integer `vehicle.speed` makes `int()` in the runner raise
  `ValueError` (`runner.py:151`).
- An `inf` stepped value on the float `engine.throttle` makes the encoder's `int()` raise
  `OverflowError` (`pids.py:80`).
- Either exception propagates out of `Dispatcher.__call__` (`ecu/dispatcher.py:78-79`).
- How the running transport then treats it was **not traced here**.

## 6. Determinism and the regression proof (for the post-V1.0 work)

### 6.1 Invariants kept (0006 §5, `runner.py:8-22`)

- A generator is still a pure function of `t`.
- `ScenarioRunner.apply` is untouched. It stays the only writer, it is idempotent for a
  fixed `t`, and it performs no await.
- **Events never repeat.** They are consumed once by their applied-marker
  (`runner.py:153-158`).

### 6.2 The proof that existing scenarios keep their current behaviour

This uses pin-then-transition. Commit 1 pins the goldens on the unchanged code, and
commit 2 changes the generator.

1. **Golden values of `ice_scenario.yaml`** (new file
   `tests/unit/test_scenario_goldens.py`, recorded before the change):
   - Timestamps:
     - every 0.5 s from 0 to 130 s;
     - each timeline `at`, and one ulp either side of it;
     - 600 s, 3600 s and 86400 s.
   - Per timestamp, recorded exactly:
     - the stored speed, rpm, coolant, throttle and fuel level;
     - the replies to `01 0C 0D 11 05 2F`, `03`, and `19 02 FF` on `0x7E1`.
   - `engine.engine_load` is driven by `sine`, so it is compared with
     `math.isclose(rel_tol=1e-12)`, and PID 0x04 is left out of the byte goldens
     (`0006:315-321`).
2. **Absent means identical**
   (`test_an_explicit_step_timeline_answers_byte_for_byte_like_an_absent_one`): the shipped
   profile and a copy with `interpolate: step` give identical replies and
   `vehicle.signals` at every golden timestamp.
3. **The old function, frozen**
   (`test_a_timeline_without_the_new_fields_matches_the_phase_7_rule`): `value_at` equals
   a verbatim copy of the Phase 7 function over the grid.
4. **Existing test files that must stay green, unmodified:**

| File | Why it matters |
|---|---|
| `tests/unit/test_scenario_profile.py` | pins `ice_scenario.yaml`'s paths (`:35-45`), speed (`:65-73`), coolant (`:76-85`) and multi-PID bytes (`:96-103`), events and replay |
| `tests/unit/test_scenario_generators.py` | six generators (`:42-44`); step timeline (`:202-232`); no transcendental function (`:236-249`) |
| `tests/unit/test_scenario_schema.py` | bad timelines refused (`:105-132`); unknown keys (`:135`) |
| `tests/unit/test_scenario_runner.py`, `tests/unit/test_scenario_wiring.py` | runner invariants |
| `tests/characterization/*` | `ice_default.yaml`, with no scenario and no runner (`conformance.md:252-254`) |
| `tests/unit/observe/test_snapshots.py` | `/vehicle` and `state` over `ice_scenario.yaml` |
| `tests/integration/test_scenario_isotp.py` | a step timeline on vcan, run on a vcan host (§1) |

Note, fact: the comment at `tests/unit/test_scenario_schema.py:229` says "Unlike a
timeline, which is read by interpolation". A timeline is not read by interpolation today
(`generators.py:155-161`). Correcting it is part of the later work.

### 6.3 New unit tests (names and assertions)

In `tests/unit/test_scenario_generators.py`:

| Test | Assertion |
|---|---|
| `test_a_timeline_defaults_to_step_without_repeat` | `interpolate == "step"` and `repeat is None` |
| `test_a_linear_timeline_interpolates_between_points` | `(0,0) (10,100)`: `value_at(2.5) == 25.0`, `value_at(5) == 50.0` |
| `test_a_linear_timeline_lands_exactly_on_its_points` | `value_at(ai) == vi` |
| `test_a_linear_timeline_holds_its_first_value_before_the_first_point` | `(5,10) (10,20)`: `value_at(0) == value_at(4.999) == 10` |
| `test_a_linear_timeline_holds_its_last_value_after_the_last_point` | `value_at(10) == value_at(1e6) == 20` |
| `test_two_points_at_one_time_make_a_linear_jump` | `(0,0) (10,50) (10,0) (20,0)`: `value_at(9.999) ≈ 49.995`, `value_at(10) == 0` |
| `test_linear_interpolation_multiplies_before_dividing` | `(9,1120) (15,2800)`: `value_at(12) == 1960.0` exactly |
| `test_the_same_float_time_gives_the_same_value` | **Determinism.** For two instances built from one configuration, and for decimal times including `15.3`, `105.3`, `0.1*3` and `89.999`: repeated calls return identical values (`==`, and the same bits via `float.hex`) |
| `test_repeat_maps_time_to_cycle_time_in_step_mode` | `(0,10) (30,20) (60,10)`, repeat 60: at `29.999, 30, 59.999, 60, 60.001, 90, 120` the values are `10, 20, 20, 10, 10, 20, 10` |
| `test_a_linear_repeat_is_continuous_at_the_boundary` | `value_at(60.0) == 10.0`, and `abs(value_at(nextafter(60,0)) − 10.0) < 1e-9` |
| `test_repeat_at_exact_multiples_gives_the_first_value` | **Boundary.** For `k` in 1…1000, and for `k = 10**9`: `value_at(k * 60.0) == 10` |
| `test_a_time_before_zero_is_the_first_value_of_a_repeating_timeline` | `value_at(-1.0) == 10` |
| `test_a_repeating_timeline_must_start_at_zero` | the §5.8.1 text |
| `test_a_repeating_timeline_must_end_at_its_repeat_time` | the last `at` below, and above, `repeat`: the §5.8.1 text |
| `test_a_repeating_timeline_must_end_where_it_began` | the §5.8.1 text |
| `test_repeat_must_be_positive` | `0` and `-5` are refused |
| `test_repeat_must_be_finite` | `nan`, `inf` and `-inf` are refused with `Input should be a finite number` |
| `test_timeline_points_must_be_finite` | `at` and `value` of `nan`, `inf` and `-inf` are refused, in both step and linear timelines |
| `test_interpolate_is_step_or_linear` | `cubic` is refused |
| `test_only_a_timeline_takes_interpolate_or_repeat` | on the other five types, both keys are refused as extra inputs |
| `test_a_linear_timeline_uses_no_transcendental_function` | the byte-exact list gains a linear repeating entry that is integral at `t=7.0` (extends `:236-249`) |

- There is deliberately **no** test that decimal times one cycle apart agree, and none that
  they differ. The first is not required. A test of the second would turn a documented
  property into a requirement.
- The known 15.3 against 105.3 example is recorded in §5.6 and pinned only as the runner
  test below.

In `tests/unit/test_scenario_schema.py`:

| Test | Assertion |
|---|---|
| `test_a_bad_repeat_is_reported_with_its_path` | the `ConfigError` text contains the §5.8.1 "must end with a point" line |
| `test_every_repeat_problem_is_reported_at_once` | all three rule failures appear in one error |
| `test_a_nonfinite_timeline_input_is_reported_with_its_path` | `.nan` or `.inf` from YAML, through `load_profile`, gives `...points.3.value: Input should be a finite number` |

In `tests/unit/test_scenario_runner.py`:

| Test | Assertion |
|---|---|
| `test_a_linear_value_is_truncated_into_an_integer_signal` | at t=15.3, the linear rpm `2454.999999999999` is stored as `2454`, and at t=105.3 it is stored as `2455`. This documents §5.6; it is not a periodicity requirement |
| `test_a_repeating_timeline_does_not_repeat_events` | a repeat-60 timeline and one event at 40: after `apply(100)` and `apply(160)`, `pending_events == 0` and the store was updated once |

## 7. The extension's demo profile (post-V1.0)

### 7.1 A new file, not an edit

- Proposed name: `src/ecu_simulator/profiles/ice_drive_cycle.yaml`. The landing is decided
  post-V1.0, per §2.
- Editing `ice_scenario.yaml` would break (fact):
  - `tests/unit/test_scenario_profile.py:35-45, 65-73, 76-85, 96-103, 115-123`;
  - `tests/unit/observe/test_snapshots.py:20-70`.
- It would also change the Phase 7 manual acceptance subject (`conformance.md:242-249`)
  and the `README.md:185` example.
- `ice_default.yaml` stays scenario-free (`conformance.md:252-254`).

### 7.2 The cycle

- 90 s, `repeat: 90`, `interpolate: linear`.
- rpm and speed share knot times, so `rpm = ratio × speed` holds between knots.

| Phase | Cycle time (s) | Speed (km/h) | rpm | Throttle (%) | Load (%) |
|---|---|---|---|---|---|
| Idle | 0–5 | 0 | 800 | 0 | 20 |
| Pull away, clutch slipping | 5–9 | 0 → 20 | 800 → 1120 | 0 → 45 by 6 s | 20 → 75 by 6 s |
| 2nd gear, 56 rpm per km/h | 9–15 | 20 → 50 | 1120 → 2800 | 45 | 75 |
| Upshift | 15–16 | 50 → 55 | 2800 → 1650 | 45 | 75 |
| Top gear, 30 rpm per km/h | 16–21 | 55 → 80 | 1650 → 2400 | 45 | 75 |
| Cruise | 21–60 | 80 | 2400 | 45 → 18 by 22 s, then 18 | 75 → 35 by 22 s, then 35 |
| Brake, engine braking | 60–70 | 80 → 26.7 | 2400 → 800 | 18 → 0 by 60 s | 10 (overrun) |
| Clutch in, rolling to a stop | 70–75 | 26.7 → 0 | 800 | 0 | 10 → 20 by 71 s |
| Idle | 75–90 | 0 | 800 | 0 | 20 |

- The coolant does not repeat: a `ramp` from 20 to 90 °C over 240 s.
- Stated simplifications:
  - the shift skips from 2nd to top gear;
  - the throttle is not lifted during the shift;
  - MAF, MAP, intake temperature and timing advance stay at their idle values
    (open question 5).

### 7.3 The file (scenario part)

The `vehicle` and `ecus` blocks are as in `ice_scenario.yaml`, with no `dtc_events`.

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

## 8. Odometer and distance

### 8.1 Facts

- `CommonState.odometer: int = 0  # km` (`vehicle/state.py:27`).
- A profile cannot set it:
  - `VehicleConfig` has no such field (`config/schema.py:84-92`);
  - unknown keys are refused (`schema.py:51-54`);
  - `build_vehicle` does not pass it (`app.py:97-103`).
- Nothing drives it.
- No PID reads it (`protocols/obd/pids.py:114-268`), and 0x31 and 0xA6 are not in the
  table.
- A scenario may drive it today (`state.py:123-136`, `schema.py:277-304`).

### 8.2 Rejected

| Option | Why it is rejected |
|---|---|
| A looping odometer ramp or timeline | Distance decreases at every loop boundary. **Rejected by the owner** |
| A non-looping `ramp` at the cycle's average speed | It rises while the car is stopped, and stops at `over`. It is not a real source |
| An accumulator in the runner | It adds runner state, and it breaks idempotence for a fixed `t` and the pure-function rule (`runner.py:8-22`, `0006:285-297`) |

### 8.3 O1: a derived `distance` generator (deferred to a later decision)

Kept as the specified real source. **It is not part of any current work.**

```yaml
- path: vehicle.odometer
  type: distance
  of: vehicle.speed      # must be a timeline in the same scenario
  from: 12000            # km at t = 0
```

- **Value:** `from + D(t) / 3600` km.
  - With `repeat`: `(k, tc) = divmod(t, repeat)`, and `D(t) = k × A + P(tc)` in closed
    form, where `A` is the per-cycle area.
  - For the §7 cycle, `A = 4360` km·s/h, which is **1.211111 km per cycle**.
- **It never decreases:**
  - every `of` value must be `>= 0`;
  - the computation is exact in `fractions.Fraction`, with one monotone rounding at the
    end;
  - a check on a 0.001 s grid from 0 to 400 s found it monotonic (Appendix A.1).
- **Cost:** about 14.5 µs per call (Appendix A.4).
- **Resolution:** `int` km, so 12001 at 58 s and 12048 at 1 h.
- **It reverses "exactly six generators"** (`0006:523`), and it lets a generator reference
  another generator's configuration. It needs its own decision record.

### 8.4 O2: no odometer in the demo (approved)

- **Approved by the owner on 2026-09-29.** Neither the stepped profile (§4) nor the
  extension profile (§7) drives `vehicle.odometer`.
- It stays 0 in state, and §9 marks it unavailable in the GUI.
- A tester loses nothing, because no PID reports it.

### 8.5 OBD PIDs 0xA6 and 0x31 (deferred to a later decision)

- **0xA6** needs a project evidence entry before any encoding is asserted
  (`docs/decisions/0003-phase-5-obd-evidence.md`, `pids.py:13-25`). Adding it would change
  the `01 A0` mask bytes, because the masks derive from the table (`pids.py:10-11`).
- **0x31** must reset when the codes are cleared, so it is not a pure function of `t`.

## 9. GUI: the odometer as "unavailable"

### 9.1 Facts

- `/vehicle` returns every signal (`observe/snapshots.py:15-21`,
  `vehicle/state.py:150-156`). WS `state` embeds the same dict (`snapshots.py:51-52`).
- 0010 §5 defines `signals` as `{dotted path → value}` (`0010:399`).
- The GUI lists every path it receives (`api/static/app.js:458-495`), shown through
  `fmtValue` (`:502-503`). `fmtValue(null)` would print `"null"` (`:109-112`).
  - There is **no** `vehicle.odometer` in the units map (`:41-46`), so the row reads
    `odometer | 0 |` with no unit. That still reads as a measurement.
- For an ICE vehicle, `vehicle.odometer` is the only signal a profile cannot set
  (`schema.py:61-92`, `state.py:18-52`). HEV and BEV also carry unconfigurable
  `battery.current`, `motor.*` and `charging.*` (`state.py:55-80`, `schema.py:78-81`).
  No PID reads any of them.

### 9.2 Options

| | (a) `odometer: int \| None = None`; `/vehicle` sends `null` | (b) the snapshot omits unsourced signals | (c) an explicit availability list |
|---|---|---|---|
| API (0010 §5) | A value can become `null`. Needs a 0010 amendment | A path disappears from `signals` | **Additive:** `/vehicle` and WS `state` gain `unavailable: ["vehicle.odometer"]`. Needs a 0010 §5 amendment |
| V1.0 code | **Yes:** `state.py:27`. Also `signal_types` uses `type(getattr(...))` (`state.py:133`), which becomes `NoneType`, so the schema would refuse to drive it (`schema.py:297-298`), and the runner's integer check changes (`runner.py:107-110`) | None | None. It is computed in `observe/` from `runtime.config.profile` |
| GUI | Must special-case `null` | The row vanishes silently | Renders `—`, with "unavailable, no source" styling and a tooltip |
| Existing tests | `test_vehicle_state.py:126` changes, and so do the schema and runner tests | Mockup data changes | None break |
| Branch and records | `modernization` and `gui`; 0006 or 0002, and 0010 | `gui`; 0010 | **`gui` only; 0010 §5** |

### 9.3 Recommendation: (c) (not yet decided by the owner)

- It changes no V1.0 code, so it fits the `gui` rule.
- The list is computed once per runtime, so it adds no per-message publisher work.
- **The rule:** a path is unavailable when no profile field can set it and no scenario
  generator drives it. Under O2, `vehicle.odometer` is unavailable on every profile,
  including the stepped demo.
- (a) is the better long-run model. It is a candidate for `modernization` after V1.0.

## 10. Expected OBD bytes for the extension profile (§7)

### 10.1 Method

- The §7 values were computed by a reference evaluator of §5.3 and §5.4 (Appendix A.1).
- They were then fed through the real simulator, as `constant` generators: the real
  schema, runner, dispatcher and encoders.

### 10.2 Truncation (fact)

1. **The runner** writes `int(value)` for integer signals (`runner.py:107-110, 151`):
   speed and rpm.
2. **The encoders** apply `int()` and clamp (`pids.py:79-84`):
   - 0x0C is `_word(rpm × 4)`;
   - 0x0D is `_byte(speed)`;
   - 0x11 and 0x04 are `_byte(% × 255 / 100)`;
   - 0x05 is `_byte(°C + 40)`.

### 10.3 Values and replies

Request `01 0C 0D 11 04 05`.

| Label | t (s) | tc (s) | rpm | Speed (km/h) | Throttle | Load | Coolant (°C) | Stored rpm, speed | Reply |
|---|---|---|---|---|---|---|---|---|---|
| idle | 2.0 | 2.0 | 800 | 0 | 0 | 20 | 20.583 | 800, 0 | `41 0C 0C 80 0D 00 11 00 04 33 05 3C` |
| mid-acceleration, 2nd gear | 12.0 | 12.0 | 1960 | 35 | 45 | 75 | 23.500 | 1960, 35 | `41 0C 1E A0 0D 23 11 72 04 BF 05 3F` |
| upshift begins | 15.0 | 15.0 | 2800 | 50 | 45 | 75 | 24.375 | 2800, 50 | `41 0C 2B C0 0D 32 11 72 04 BF 05 40` |
| mid-upshift | 15.5 | 15.5 | 2225 | 52.5 | 45 | 75 | 24.521 | 2225, **52** | `41 0C 22 C4 0D 34 11 72 04 BF 05 40` |
| upshift ends, top gear | 16.0 | 16.0 | 1650 | 55 | 45 | 75 | 24.667 | 1650, 55 | `41 0C 19 C8 0D 37 11 72 04 BF 05 40` |
| cruise | 40.0 | 40.0 | 2400 | 80 | 18 | 35 | 31.667 | 2400, 80 | `41 0C 25 80 0D 50 11 2D 04 59 05 47` |
| mid-brake | 65.0 | 65.0 | 1600 | 53.333 | 0 | 10 | 38.958 | 1600, **53** | `41 0C 19 00 0D 35 11 00 04 19 05 4E` |
| just before the boundary | 89.999 | 89.999 | 800 | 0 | 0 | 20 | 46.250 | 800, 0 | `41 0C 0C 80 0D 00 11 00 04 33 05 56` |
| one ulp before the boundary | 89.99999999999999 | 89.99999999999999 | 800 | 0 | 0 | 20 | 46.250 | 800, 0 | `41 0C 0C 80 0D 00 11 00 04 33 05 56` |
| **exactly at the boundary** | 90.0 | **0.0** | 800 | 0 | 0 | 20 | 46.250 | 800, 0 | `41 0C 0C 80 0D 00 11 00 04 33 05 56` |
| just after the boundary | 90.001 | 0.0010000000000047748 | 800 | 0 | 0 | 20 | 46.250 | 800, 0 | `41 0C 0C 80 0D 00 11 00 04 33 05 56` |
| 2nd cycle, mid-acceleration | 102.0 | 12.0 | 1960 | 35 | 45 | 75 | **49.750** | 1960, 35 | `41 0C 1E A0 0D 23 11 72 04 BF 05 59` |
| 2nd cycle, cruise | 130.0 | 40.0 | 2400 | 80 | 18 | 35 | **57.917** | 2400, 80 | `41 0C 25 80 0D 50 11 2D 04 59 05 61` |
| 2nd boundary | 180.0 | 0.0 | 800 | 0 | 0 | 20 | 72.500 | 800, 0 | `41 0C 0C 80 0D 00 11 00 04 33 05 70` |
| warm, 4th cycle, cruise | 310.0 | 40.0 | 2400 | 80 | 18 | 35 | 90.000 | 2400, 80 | `41 0C 25 80 0D 50 11 2D 04 59 05 82` |

- **The loop boundary is continuous.** The cycle-driven PIDs are unchanged across
  89.999 → 90.0 → 90.001.
- **The coolant does not reset:** `05 3F` at t=12, `05 59` at t=102.
- **The odometer, under O1 only (deferred).** Appendix A.1 also lists the values O1 would
  produce: 12000.034 km at 12 s, 12001.211 km at 90 s, 12002.422 km at 180 s. They would
  appear in the GUI only.
- **Decimal times one cycle apart (§5.6):**

| t | tc | rpm (generator) | Stored rpm | 0x0C bytes |
|---|---|---|---|---|
| 15.3 | 15.3 | 2454.999999999999 | 2454 | `26 58` |
| 105.3 | 15.299999999999997 | 2455.000000000003 | 2455 | `26 5C` |

### 10.4 Framing (inferred, not computed)

- `obd_physical` pads with `0x00` (`ice_scenario.yaml:158-164`).
- The 12-byte reply needs an ISO-TP first frame and one consecutive frame.
- No wire capture is claimed.

## 11. Open questions for the owner

1. **The sooner path (§4):** build the stepped profile now, as a no-code stopgap?
2. **Its location (§4.5):** L3 `docs/examples/` on `gui` (recommended), L1, L2 or L4?
3. **Its step:** `interval: 1` with 90 values (recommended), or coarser?
4. **The GUI (§9):** option (c), recommended? And should the list name a reason, for
   example `{"vehicle.odometer": "no_source"}`?
5. **The demo's realism, for both profiles:**
   - a third gear;
   - a throttle lift during the shift;
   - driving MAF and MAP.
6. **DTC events** in the demo: none (proposed), or P0128 while the engine is cold?
7. **Cycle and warm-up:** 90 s and 240 s?
8. **The §5.8.2 finding:** should finiteness also be enforced on the existing generators,
   `tick` and event `at`? That is a V1.0 schema tightening, so it is a separate decision.

The extension's landing, O1 and PIDs 0xA6 and 0x31 are already deferred by the owner
(§2), and are not asked again.

## 12. Implementation plan outline (only after approval)

Each task is test-first, in its own commit, with no amend.

**Now, if the owner approves §4 and §9 (`gui` only; no V1.0 code):**

| # | Task | Files | Tests |
|---|---|---|---|
| N1 | The stepped profile | `docs/examples/ice_drive_cycle_stepped.yaml` (if L3), and its generating script beside it or in the plan | new `tests/unit/test_drive_cycle_stepped_example.py`: it loads through `load_profile`; four stepped paths plus the coolant `ramp`; **every list has exactly 90 values with `interval: 1`**; the §4.3 rows as byte assertions; rpm = 56 × speed on steps 9–15 and 30 × speed on steps 16–21; the coolant rises from t=12 to t=102; `vehicle.odometer` not driven |
| N2 | Direct verification (plan §10) | `docs/validation/` | an in-process run of §4.3, and a vcan run with `--profile` and `--api`, recorded on a vcan host |
| N3 | The `unavailable` list | `observe/snapshots.py`, `api/static/app.js`, CSS, 0010 §5 amendment | `test_snapshots.py`: `ice_default` gives `["vehicle.odometer"]`, a profile that drives it gives `[]`, and it is present in WS `state`; the HTTP key; the manual rendering checklist (0010 §7) |

**Post-V1.0, where the owner then decides (§2):**

| # | Task | Files | Tests |
|---|---|---|---|
| P0 | A decision record amending 0006 §5.1 and §5.3 | `docs/decisions/` | — |
| P1 | **Pin** the `ice_scenario.yaml` goldens on unchanged code | `tests/unit/test_scenario_goldens.py` | §6.2 items 1 and 3 |
| P2 | The fields, the closing-point rule and the finiteness checks | `scenario/generators.py` (`TimelineSignal`, `TimelinePoint`) | §6.3 validation tests |
| P3 | Linear and repeat evaluation | `scenario/generators.py` | §6.3 value tests; §6.2 still green |
| P4 | Correct the stale comment | `tests/unit/test_scenario_schema.py:229` | — |
| P5 | The linear profile, replacing the stepped example | `ice_drive_cycle.yaml` wherever P0 puts it | §10.3 rows at whole-second times; coolant not reset; rpm/speed ratios; throttle and load by phase |
| P6 | Direct verification and docs | `docs/validation/`, `conformance.md:217`, `README.md` | — |
| — | O1 and PIDs 0xA6/0x31 | only after their own later decision | — |

---

## Appendix A. The extension: commands and output

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

# ... TIMES list as in §10.3, one row printed per time, then a monotonicity check of
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

### A.2 Decimal times, and the step and linear boundary example (`extra.py`, which reuses A.1's functions)

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

`fmod` is exact on its inputs. `0.1*3` is not `0.3`, so its remainder is not zero. That is
why §5.6 requires determinism in the double `t`, not in decimal time.

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

## Appendix B. The stepped profile through the real generators (`stepped_profile.py`)

The script samples the §7 knot lines at whole seconds into 90-value lists. It writes a
profile made of `ice_scenario.yaml`'s header and `vehicle` block, the stepped scenario of
§4.2, and `ice_scenario.yaml`'s `ecus` block without `dtc_events`. It loads that file with
the real `load_profile`, and asks the real `Dispatcher` at each `t` on a fresh runtime
driven by `SimulatedClock`. The scenario section written to the file is exactly the YAML
in §4.2.

```python
KNOTS = {  # the §7 knots, used only to author the lists
    "vehicle.speed": [(0, 0), (5, 0), (21, 80), (60, 80), (75, 0), (90, 0)],
    "engine.rpm": [(0, 800), (5, 800), (9, 1120), (15, 2800), (16, 1650), (21, 2400), (60, 2400), (70, 800), (90, 800)],
    "engine.throttle": [(0, 0), (5, 0), (6, 45), (21, 45), (22, 18), (59, 18), (60, 0), (90, 0)],
    "engine.engine_load": [(0, 20), (5, 20), (6, 75), (21, 75), (22, 35), (59, 35), (60, 10), (70, 10), (71, 20), (90, 20)],
}

def sample(points, t):
    for (a0, v0), (a1, v1) in zip(points, points[1:]):
        if a0 <= t < a1:
            return v0 + ((v1 - v0) * (t - a0)) / (a1 - a0)
    return points[-1][1]

# values = [str(int(sample(pts, i))) if path == "vehicle.speed" else fmt(sample(pts, i)) for i in range(90)]
# ... written as `type: stepped, interval: 1`, ten values per line, plus the coolant ramp.

profile = load_profile(out)                       # real ruamel loader + schema

def ask(t, req="010C0D110405"):
    clock = SimulatedClock()
    rt = app.build_runtime(app.RuntimeConfig.build(profile), clock=clock)
    clock.advance(t)
    r = rt.dispatcher(DiagnosticRequest(bytes.fromhex(req), 0x7E0))
    return rt.vehicle.signals, r.payload.hex(" ").upper()
```

```
$ .venv/bin/python scratchpad/stepped_profile.py scratchpad/ice_drive_cycle_stepped.yaml
valid: True [('vehicle.speed', 'stepped', 90), ('engine.rpm', 'stepped', 90), ('engine.throttle', 'stepped', 90), ('engine.engine_load', 'stepped', 90), ('engine.coolant_temp', 'ramp', None)]
label                                             t | step | rpm  spd  thr   load  cool    | 01 0C 0D 11 04 05 ->
idle                                            2.0 |    2 |  800   0   0.0  20.0  20.583 | 41 0C 0C 80 0D 00 11 00 04 33 05 3C
mid-acceleration, 2nd gear                     12.0 |   12 | 1960  35  45.0  75.0  23.500 | 41 0C 1E A0 0D 23 11 72 04 BF 05 3F
same step, half a second later                 12.5 |   12 | 1960  35  45.0  75.0  23.646 | 41 0C 1E A0 0D 23 11 72 04 BF 05 3F
upshift begins                                 15.0 |   15 | 2800  50  45.0  75.0  24.375 | 41 0C 2B C0 0D 32 11 72 04 BF 05 40
t=15.3                                         15.3 |   15 | 2800  50  45.0  75.0  24.462 | 41 0C 2B C0 0D 32 11 72 04 BF 05 40
mid-upshift                                    15.5 |   15 | 2800  50  45.0  75.0  24.521 | 41 0C 2B C0 0D 32 11 72 04 BF 05 40
upshift ends, top gear                         16.0 |   16 | 1650  55  45.0  75.0  24.667 | 41 0C 19 C8 0D 37 11 72 04 BF 05 40
cruise                                         40.0 |   40 | 2400  80  18.0  35.0  31.667 | 41 0C 25 80 0D 50 11 2D 04 59 05 47
brake, first step                              61.0 |   61 | 2240  74   0.0  10.0  37.792 | 41 0C 23 00 0D 4A 11 00 04 19 05 4D
mid-brake                                      65.0 |   65 | 1600  53   0.0  10.0  38.958 | 41 0C 19 00 0D 35 11 00 04 19 05 4E
just before the boundary                     89.999 |   89 |  800   0   0.0  20.0  46.250 | 41 0C 0C 80 0D 00 11 00 04 33 05 56
one ulp before                    89.99999999999999 |   89 |  800   0   0.0  20.0  46.250 | 41 0C 0C 80 0D 00 11 00 04 33 05 56
exactly at the boundary                        90.0 |    0 |  800   0   0.0  20.0  46.250 | 41 0C 0C 80 0D 00 11 00 04 33 05 56
just after the boundary                      90.001 |    0 |  800   0   0.0  20.0  46.250 | 41 0C 0C 80 0D 00 11 00 04 33 05 56
2nd cycle, mid-acceleration                   102.0 |   12 | 1960  35  45.0  75.0  49.750 | 41 0C 1E A0 0D 23 11 72 04 BF 05 59
t=105.3                                       105.3 |   15 | 2800  50  45.0  75.0  50.712 | 41 0C 2B C0 0D 32 11 72 04 BF 05 5A
2nd cycle, cruise                             130.0 |   40 | 2400  80  18.0  35.0  57.917 | 41 0C 25 80 0D 50 11 2D 04 59 05 61
2nd boundary                                  180.0 |    0 |  800   0   0.0  20.0  72.500 | 41 0C 0C 80 0D 00 11 00 04 33 05 70
warm, 4th cycle, cruise                       310.0 |   40 | 2400  80  18.0  35.0  90.000 | 41 0C 25 80 0D 50 11 2D 04 59 05 82
```

## Appendix C. Nonfinite input today, and the proposed messages

YAML loading through the project's loader (`config/loader.py:22-26`):

```
$ .venv/bin/python -   # _reader().load("a: .nan\nb: .inf\nc: -.inf\nd: nan\ne: 1e400\nf: .NaN")
{'a': nan, 'b': inf, 'c': -inf, 'd': 'nan', 'e': inf, 'f': nan}
```

`ice_scenario.yaml` with one nonfinite edit per case, through `parse_profile`:

```
timeline value nan -> ACCEPTED
timeline at inf (last) -> ACCEPTED
timeline at nan (last) -> scenario.signals.0.timeline.points.10.at: Input should be greater than or equal to 0
ramp over inf -> ACCEPTED
ramp to nan -> ACCEPTED
sine period nan -> scenario.signals.3.sine.period: Input should be greater than 0
sine centre inf -> ACCEPTED
stepped value inf -> ACCEPTED
stepped interval inf -> ACCEPTED
constant value nan -> ACCEPTED
sequence for inf -> ACCEPTED
tick inf -> ACCEPTED
event at inf -> ACCEPTED
engine.coolant_temp nan (initial) -> ACCEPTED
engine.throttle nan (initial, bounded) -> vehicle.engine.throttle: Input should be less than or equal to 100
```

What an accepted value does on a request (`01 0D 11` through the real `Dispatcher`):

```
float throttle inf -> raises OverflowError cannot convert float infinity to integer
int speed nan -> raises ValueError cannot convert float NaN to integer
```

The proposed constraint, on Pydantic 2.13.5 (`Field(..., allow_inf_nan=False)`):

```
{'repeat': nan}  [(('repeat',), 'finite_number', 'Input should be a finite number')]
{'repeat': inf}  [(('repeat',), 'finite_number', 'Input should be a finite number')]
{'repeat': -inf} [(('repeat',), 'finite_number', 'Input should be a finite number')]
{'at': nan, 'value': 1}  [(('at',), 'finite_number', 'Input should be a finite number')]
{'at': inf, 'value': 1}  [(('at',), 'finite_number', 'Input should be a finite number')]
{'at': 1, 'value': nan}  [(('value',), 'finite_number', 'Input should be a finite number')]
{'at': 1, 'value': -inf} [(('value',), 'finite_number', 'Input should be a finite number')]
```
