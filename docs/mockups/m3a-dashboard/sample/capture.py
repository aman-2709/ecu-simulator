"""Task 16 capture: drive a real simulator with ISO-TP in a private netns, then GET the API.

Run inside: unshare -r -n (with lo up and a private vcan0).
"""
import os, signal, subprocess, sys, threading, time, json
from pathlib import Path
import isotp

IFACE = "vcan0"
PORT = 8765
OUT = Path(sys.argv[1])
PROFILE = sys.argv[2]
CAN_ISOTP_SF_BROADCAST = 0x0800

def sock(rx, tx, broadcast=False, pad=True):
    s = isotp.socket(timeout=1.0)
    flags = isotp.socket.flags.TX_PADDING if pad else 0
    if broadcast:
        flags |= CAN_ISOTP_SF_BROADCAST
    s.set_opts(optflag=flags, txpad=0)
    s.bind(IFACE, isotp.Address(isotp.AddressingMode.Normal_11bits, rxid=rx, txid=tx))
    return s

cmd = [sys.executable, "-m", "ecu_simulator", "--interface", IFACE,
       "--api", f"127.0.0.1:{PORT}", "--profile", PROFILE]
print("CMD:", " ".join(cmd), flush=True)
proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True)
ready = threading.Event()
lines = []
def pump():
    for line in proc.stderr:
        lines.append(line)
        if "ecu-simulator ready on" in line:
            ready.set()
threading.Thread(target=pump, daemon=True).start()
if not ready.wait(15):
    proc.kill(); print("".join(lines)); sys.exit("not ready")
print("READY:", [l for l in lines if "ready on" in l][0].strip(), "pid", proc.pid, flush=True)

func_tx = sock(0, 0x7DF, broadcast=True)
phys = sock(0x7E8, 0x7E0)          # obd_physical; also receives functional replies
uds = sock(0x7E9, 0x7E1, pad=False)

log = []
def functional(hexreq):
    func_tx.send(bytes.fromhex(hexreq))
    try: r = phys.recv().hex()
    except TimeoutError: r = None
    log.append({"t": time.time(), "via": "7DF->7E8", "req": hexreq, "resp": r})
def physical(s, via, hexreq):
    s.send(bytes.fromhex(hexreq))
    try: r = s.recv().hex()
    except TimeoutError: r = None
    log.append({"t": time.time(), "via": via, "req": hexreq, "resp": r})

def snooze(n): time.sleep(n)

t0 = time.time()
def until(sec):
    d = t0 + sec - time.time()
    if d > 0: snooze(d)

def round_():
    for r in ["010c", "010d", "0105"]:
        functional(r)
    functional("0111"); functional("012f")

# Round 1: engine cold, idle.
until(3); round_(); functional("0902"); functional("03")
physical(phys, "7E0->7E8", "0100")
physical(uds, "7E1->7E9", "22f190")
physical(uds, "7E1->7E9", "1902ff")
# Round 2: pending P0128 after 40 s.
until(44); round_(); functional("07"); functional("03")
physical(uds, "7E1->7E9", "1902ff")
# Round 3: confirmed + MIL after 75 s.
until(78); round_(); functional("0101"); functional("03"); functional("07")
physical(uds, "7E1->7E9", "1902ff")
physical(phys, "7E0->7E8", "0105")
functional("010c")
snooze(0.6)

(OUT / "tester_log.json").write_text(json.dumps(log, indent=2) + "\n")
for ep in ["status", "vehicle", "dtcs", "ecus", "exchanges"]:
    body = subprocess.run(["curl", "-sS", "--fail", "-H", f"Host: 127.0.0.1:{PORT}",
                           f"http://127.0.0.1:{PORT}/api/v1/{ep}"], check=True, capture_output=True).stdout
    (OUT / f"{ep}.json").write_text(json.dumps(json.loads(body), indent=2) + "\n")
    print("GOT", ep, len(body), flush=True)
os.kill(proc.pid, signal.SIGINT)
rc = proc.wait(10)
print("EXIT", rc, flush=True)
(OUT / "simulator_stderr.txt").write_text("".join(lines))
