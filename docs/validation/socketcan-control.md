# SocketCAN control GUI acceptance

## Revisions and baseline

On 2026-11-19, the isolated integration branch combined the approved phase-12 tip `c21ffdf69b9a7b3fcd5fa251b03bce3b86293a87` (runtime `f1d377daa2208154df02abcb6a0ff39d39f18ff2`) and GUI `d0f1cc0eec87e0da893dba75b5ac6a2e6905481e` at `1acd9f5cff9bcf91bdb1cc4b98d24a2126be6932`.

The initial combined run passed 85 kernel integration tests and exposed three historical characterization assertions for SIDs 27 and 2E. The pinned phase-12 runtime already claimed both and returned the session-gated NRC 7F for the selected inputs. Commit `f767f5f981d4fd2d2b58952e7dc70d489aa49638` updated the assertions. Before GUI implementation, `pytest tests/unit tests/characterization -q` passed 1,532 tests with one optional-dependency skip and three existing expected failures. `bash scripts/run_integration_tests.sh -q` passed all 85 integration tests, including the observer WebSocket exchange.

## Browser and wire evidence

The GUI tests start the manager and ECU in separate processes, drive headless Chrome through Playwright, and use Linux kernel ISO-TP on Classical CAN. These checks use the same tester code for both interfaces.

| Scenario | Browser assertion | Independent bus assertion |
| --- | --- | --- |
| Single configured fault | `59 02 8C 00 01 01 08`, P0001 confirmed | Endpoint 7E1/7E9. Separate raw-frame capture applies to the two-record case below. |
| Two configured faults on vcan0 | `59 02 8C 00 01 01 08 94 77 01 04`, P0001 confirmed and B1477 pending | CAN_RAW capture in order: 7E9 first frame `10 0B 59 02 8C 00 01 01`, 7E1 flow control beginning `30`, 7E9 consecutive frame beginning `21 08 94 77 01 04` |
| Redirected response on vcan0 | Both records decoded after sending on 7E1 | ISO-TP reassembly uses the configured return socket, RX 7E8 and flow control on 7E0 |

Additional browser assertions cover start/stop/restart, named-profile persistence, unchanged shipped bytes, invalid YAML, failed startup and retry, stale tabs, busy exchanges, negative responses and suppression silence. The controlled tester test delivers a negative response 450 ms into a 500 ms deadline.

## Final software verification

At code revision `28ffd00aecdbcffdbedf293068b9d99e28d58b1d`, the full default pytest suite ran in a private network namespace with loopback and vcan0 up. It reported **1,636 passed, one skipped and three expected failures**. The skip checks absence of aiohttp in an environment with the GUI extra installed. The three existing expected failures are DEV-26, DEV-11 and DEV-03. Ruff and mypy passed. A built wheel contained the GUI console entry point and all three control assets.

```sh
unshare -rn bash -ec '
  ip link set lo up
  ip link add vcan0 type vcan
  ip link set vcan0 up
  exec .venv/bin/python -m pytest -q -ra
'
```

## Physical bench

The owner identified a dedicated terminated bench with a second node. The first attempt used the same browser test with `ECU_SIM_CAN_IFACE=can0` on a gs_usb adapter configured for 500 kbit/s. The single-P0001 raw payload, decoded status and Stop assertions passed. On the second run the two-DTC exchange timed out. Across the attempt, the controller moved from ERROR-ACTIVE to ERROR-PASSIVE. Error-warning events increased from 36 to 40 and error-passive events from 1 to 2. The bus-off counter remained zero. These counters do not identify which frame failed. Physical multi-frame acceptance remains open.
