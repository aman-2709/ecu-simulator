# Captured sample for the M3a mockup

These files are **real simulator output**, captured once and replayed statically by the
mockup. Nothing in the mockup is connected to a simulator.

- Commit: `aa8cffe6f896e059edb5e66259af444dbb227d97` (branch `gui`)
- Date: 2026-09-28T20:14:04Z (simulator ready) to about 20:15:25Z (SIGINT)
- Profile: `src/ecu_simulator/profiles/ice_scenario.yaml`
- Interface: a private `vcan0`, created inside `unshare -r -n`. The host's `vcan0` and
  `can0` were not touched.

## Files

| File | What it is |
|---|---|
| `status.json`, `vehicle.json`, `dtcs.json`, `ecus.json`, `exchanges.json` | The bodies of `GET /api/v1/{status,vehicle,dtcs,ecus,exchanges}`, re-indented with `json.dumps(indent=2)`; values unchanged |
| `tester_log.json` | What the ISO-TP tester sent and received, from the tester side |
| `simulator_stderr.txt` | The simulator's own log for the run |
| `capture.py` | The script that drove the run |
| `capture-meta.json` | Commit, date and profile, read by `../build_sample_data.py` |

## Exact commands

Run from the worktree root:

```sh
unshare -r -n bash -c 'ip link set lo up \
  && ip link add dev vcan0 type vcan && ip link set up vcan0 \
  && ip -br link \
  && .venv/bin/python <scratchpad>/capture.py docs/mockups/m3a-dashboard/sample src/ecu_simulator/profiles/ice_scenario.yaml'
```

`capture.py` then:

1. starts `.venv/bin/python -m ecu_simulator --interface vcan0 --api 127.0.0.1:8765
   --profile src/ecu_simulator/profiles/ice_scenario.yaml` and waits for
   `ecu-simulator ready on` on its stderr;
2. sends real ISO-TP requests with `isotp` sockets, in three rounds timed from the ready
   line so the scenario's trouble code is seen in each state:
   - t ≈ 3 s (P0128 clear): functional `01 0C`, `01 0D`, `01 05`, `01 11`, `01 2F`, `09 02`,
     `03`; physical `01 00` on 0x7E0; `22 F190` and `19 02 FF` on 0x7E1;
   - t ≈ 44 s (P0128 pending, raised at 40 s): the five mode 01 requests, `07`, `03`, and
     `19 02 FF` on 0x7E1;
   - t ≈ 78 s (P0128 confirmed and MIL requested, at 75 s): the five mode 01 requests,
     `01 01`, `03`, `07`, `19 02 FF` on 0x7E1, `01 05` on 0x7E0, and a last functional
     `01 0C`;
3. runs `curl -sS --fail -H 'Host: 127.0.0.1:8765' http://127.0.0.1:8765/api/v1/<endpoint>`
   for `status`, `vehicle`, `dtcs`, `ecus` and `exchanges`;
4. stops the simulator with `os.kill(pid, SIGINT)` on its exact PID; it exited 0.

The run used a copy of `capture.py` in a scratch directory. The only difference from the
copy here is an unused `urllib.request` import, removed afterwards.

## What the capture shows, as it is

- 29 exchanges, `seq` 1 to 29 with no gap; 26 `responded`, 3 `no_response` (OBD mode
  `07` twice, which the simulator does not implement, and PID `01 01`, which the profile
  does not support).
- `22 F190` got a negative response `7f 22 11`; the outcome is still `responded`,
  because the dispatcher returned bytes.
- `summary` for `01 00` reads "unknown parameter": that is the publisher's text as built
  at this commit, not an edit.
- No WebSocket client was connected, so `clients` is 0 and no `dropped` message exists.

## Hand-made items

`../build_sample_data.py` appends these to the page data after `seq` 29, each with
`"_provenance": "synthetic"` and a note; the page tags every one of them **synthetic**:

- `seq` 30, `unrouted` (not reachable in v1 as built, 0010 §4.4);
- `seq` 31, `error` with `KeyError`;
- a gap, `seq` 32 to 35, and a `dropped` message (`client_dropped` 4) that explains it;
- `seq` 36, a 600-byte response retained to 512 bytes (`response_truncated`);
- `seq` 37, the `encode_failed` fallback event.
