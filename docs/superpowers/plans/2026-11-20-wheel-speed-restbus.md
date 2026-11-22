# Wheel-speed restbus implementation plan

Use test-driven development and separate implementation tasks.

1. Bundle the licensed pinned database and packer support. Implement WheelCodec in restbus.py with encode(speed), decode(data) and metadata(). Test the independent byte fixtures, asymmetric wheel ordering and database provenance.
2. Add optional restbus profile configuration, the wheel_speed_demo profile, and monotonic raw SocketCAN publisher in the existing runtime. Synchronize vehicle state before each sample. Cover readiness, cadence, transmit failure, skipped slots and cleanup.
3. Extend the run manager and child with acknowledged, run-scoped pause/resume. Expose metadata and pause state in run status. Keep the existing child readiness behavior for profiles without a publisher. Check stale requests.
4. Decode actual capture data with the active run and interface. Render four named speeds, raw data, period, age and staleness in Live CAN. Add simulator pause and resume controls and cover malformed frames and run changes.
5. Run browser and independent CAN capture acceptance on private vcan0, including the OBD cross-check and silence after stop. Run the regression suites. Check physical bench availability separately.
