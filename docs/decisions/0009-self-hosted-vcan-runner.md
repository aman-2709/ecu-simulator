# 0009 — A self-hosted runner for the vcan integration suite

Status: **Proposed.** The owner agreed the direction on 2026-09-25: a separate private
repository, a disposable VM for each run, and no result reporting and no required check to
begin with (§4). The cloud provider and the budget are **still to be decided** (§7).

**Nothing in this record has been executed.** No runner is registered, no VM or cloud
account exists, no workflow file has changed, no repository has been created and no
credential has been issued. The owner performs or approves every step in §5.

## 1. Problem

`tests/integration` (53 cases) and `tests/integration/test_elm327_simulated.py` (15 cases)
need a kernel that can create `CAN_ISOTP` sockets. Every GitHub-hosted Ubuntu runner boots
an Azure kernel that has no `can_isotp` module:

- `modprobe can_isotp` fails with *"Module can_isotp not found in
  /lib/modules/6.17.0-1022-azure"*; `vcan` and `CAN_RAW` both work.
- A `packages.ubuntu.com` contents search for `can-isotp.ko.zst` in noble/amd64 returns
  `linux-modules-*` packages for gke, ibm, oem, oracle, gcp, aws, generic and lowlatency,
  and **no azure flavour at all**, so installing `linux-modules-extra` gets nowhere.

So today a green CI run means those 68 cases were **skipped**, not that they passed. The
annotation added in `68859fa` says so by name.

## 2. Requirements

These come from the project owner and are hard constraints, not preferences.

| # | Requirement |
|---|---|
| R1 | A dedicated Linux VM. **Not** the development PC. |
| R2 | `vcan` only. **No** physical CAN hardware is attached or passed through: no USB, no `can0`, no serial devices. |
| R3 | **Ephemeral**, and preferably a cloud VM: one VM per run, destroyed afterwards. |
| R4 | The job runs **only on trusted pushes to `modernization`**. Never on `pull_request` and never on code from a fork. |
| R5 | The kernel must be able to create and bind a `CAN_ISOTP` socket. This has to be **measured on the image**, not assumed from a package listing (§5, step 3). |
| R6 | A green result has to mean the cases **passed**. On this runner a skip is a failure. |

Derived requirements:

| # | Requirement | Why |
|---|---|---|
| D1 | The runner account has **no sudo**. | `scripts/run_integration_tests.sh` only needs unprivileged user and network namespaces (`unshare -r -n`), and it creates `vcan0` inside them. Nothing on the host needs root at job time. |
| D2 | `vcan` and `can_isotp` are **preloaded in the image** (`/etc/modules-load.d/`). | Without sudo the job can't `modprobe`; the wrapper relies on the kernel autoloading them. Preloading takes that dependency away. |
| D3 | Unprivileged user namespaces work for the runner account. | Ubuntu 24.04 can restrict them through AppArmor (`kernel.apparmor_restrict_unprivileged_userns`). The wrapper works on the development host; **I have not measured the VM image and cannot know until it exists.** Step 3 checks it. |
| D4 | The VM gets outbound HTTPS only and **no inbound access**. | It needs github.com and PyPI. It has no reason to reach anything else, and nothing needs to reach it. |
| D5 | The token that registers runners **never enters the VM**. | Otherwise a job could read it and register its own runners. |

## 3. Why the obvious design does not meet R4

The obvious design registers the runner on the public repository and guards the job:

```yaml
if: github.event_name == 'push' && github.ref == 'refs/heads/modernization'
runs-on: [self-hosted, vcan-isotp]
```

**On a public repository that guard is not a security boundary.** A `pull_request` run
executes the workflow files **from the pull request itself**. A fork can remove the `if:`
or add a new workflow with `on: pull_request` and `runs-on: [self-hosted, vcan-isotp]`, and
its code then runs on the runner. The only thing standing in the way is the repository
setting that requires approval for fork workflow runs, and that is a person clicking a
button, not a technical control. GitHub's own documentation says the same thing: self-hosted
runners *"should generally not be used for public repositories"*, because *"forks of the
repository can execute dangerous code on the runner machine by creating pull requests that
trigger workflows."*

Runner groups, which can deny public repositories access to a runner, exist only for
organisations and enterprises. This repository belongs to a personal account.

## 4. Agreed direction: a private repository and a disposable cloud VM for each run

### 4.1 The runner belongs to a private repository

Register runners only on a **separate private repository**. It contains nothing but trusted
`modernization` commits, pushed there by the owner. Call it `ecu-simulator-vcan-ci` here;
the name doesn't matter.

```
owner ──git push──▶ public  aman-2709/ecu-simulator          hosted runners only, unchanged
      └─git push──▶ private aman-2709/ecu-simulator-vcan-ci  the only place a self-hosted runner is ever registered
                                  │
                                  └── per run: create VM → one job → destroy VM
```

- **Forks can't reach it.** A fork of the public repository has nothing to do with the
  private one. No workflow file in the public repository, edited or not, can target a
  runner that isn't registered to it. This is what makes R4 hold, rather than a guard
  holding it.
- **Only trusted commits.** The owner's push is the only way code gets into the private
  repository. Pushing to both is one git setting on the owner's side: a second push URL on
  `origin`.
- **The public repository gets no secrets.** Every credential in this design lives in the
  private repository.

### 4.2 A disposable cloud VM for each run (R1, R3, D5)

The owner prefers an ephemeral cloud VM to a permanent host. **The first thing to evaluate
is creating and destroying a VM for every run.** A permanent VM host is designed only if
that proves unworkable (§6).

One run of the workflow in the private repository has three jobs:

1. **`start`**, on a GitHub-hosted runner. It asks GitHub for a **just-in-time runner
   configuration** for the private repository
   (`POST /repos/{owner}/ecu-simulator-vcan-ci/actions/runners/generate-jitconfig`), with a
   label unique to this run. It then creates a VM through the provider's API, passing
   **only that JIT configuration** in the VM's user data. A JIT runner accepts exactly one
   job and then deregisters. The token that can mint runners stays in the hosted job and
   never enters the VM (D5).
2. **`vcan`**, on the new VM, selected by that unique label. It runs the suite (§4.5).
3. **`stop`**, on a GitHub-hosted runner, with `if: always()`. It deletes the VM whether
   `vcan` passed, failed or never started.

Two guards cover a `stop` that doesn't run:

- **The provider enforces a maximum lifetime**, through the provider's own
  auto-termination or a scheduled sweeper that deletes anything tagged for this purpose
  that is older than an hour.
- **A budget alert** on the cloud account.

Credential scopes, all stored as secrets in the **private** repository only:

| Secret | Scope |
|---|---|
| GitHub token for JIT configuration | Fine-grained, **private repository only**, *Administration: read and write* (needed to mint JIT runners) |
| Cloud credential | Allowed only to create, tag and delete instances of one small type in one project or region, with no other permissions. Prefer OIDC federation from Actions to a long-lived key if the provider supports it |

Two limits of this design have to be stated:

- The `start` and `stop` jobs are GitHub-hosted jobs in a **private** repository, so they
  use the account's private-repository Actions minutes. That is small per run, but it
  isn't free.
- The provider's kernel matters (§4.3).

### 4.3 VM image (R2, R5, D1–D4)

- **The kernel flavour must ship `can_isotp`.** The contents search in §1 found
  `can-isotp.ko.zst` in the gke, ibm, oem, oracle, gcp, aws, generic and lowlatency
  flavours, and **not azure**. **An Azure VM is therefore ruled out**, for the same reason
  the hosted runners fail. The package listing is only a hint: R5 requires a measurement
  on the actual image (§5, step 3).
- Ubuntu 24.04. Either `vcan` and `can_isotp` are listed in `/etc/modules-load.d/can.conf`,
  or user data runs `modprobe` for both **before** the runner starts, as root, while no
  job code is present yet.
- `python3.12`, `python3.12-venv`, `iproute2`, `util-linux`, `git`, and the GitHub runner
  application. The runner application can be installed from user data, at the cost of
  boot time, or baked into a custom image.
- A `runner` user with no sudo.
- Network: **no inbound rules at all**, outbound HTTPS only. There is no home LAN in the
  picture, which is one advantage over a self-hosted box (D4). No attached volumes beyond
  the boot disk, and no USB, serial or CAN devices (R2).

### 4.4 Reporting and required checks: not initially

- **Nothing is reported back to the public repository at first.** Results are read in the
  private repository's Actions tab, and no token with write access to the public
  repository is created.
- **The job is not a required check** for anything, including merges to `master`, until
  its reliability and security have been demonstrated over a sustained series of runs.
  Reporting a commit status back would be revisited at the same point.

### 4.5 Proposed workflow

Lives only in the private repository, at `.github/workflows/vcan-self-hosted.yml`. The
provider commands are placeholders until a provider is chosen. The `if:` on the jobs only
keeps the file quiet if it is ever pushed to the public repository. It is not the security
boundary; §4.1 is.

```yaml
name: vcan integration (disposable VM)

on:
  push:
    branches: [modernization]

permissions:
  contents: read

concurrency:
  group: vcan-disposable-vm
  cancel-in-progress: false

jobs:
  start:
    if: github.repository == 'aman-2709/ecu-simulator-vcan-ci'
    runs-on: ubuntu-latest
    timeout-minutes: 10
    outputs:
      vm: ${{ steps.vm.outputs.id }}
      label: ${{ steps.jit.outputs.label }}
    steps:
      - id: jit
        env:
          GH_TOKEN: ${{ secrets.RUNNER_ADMIN_TOKEN }}
        run: |
          label="vcan-${{ github.run_id }}-${{ github.run_attempt }}"
          gh api -X POST "repos/${{ github.repository }}/actions/runners/generate-jitconfig" \
            -f name="$label" -F runner_group_id=1 -f 'labels[]=self-hosted' -f "labels[]=$label" \
            --jq .encoded_jit_config > jit.txt
          echo "label=$label" >> "$GITHUB_OUTPUT"
      - id: vm
        run: |
          # PLACEHOLDER: provider CLI. Create one small Ubuntu 24.04 VM (non-Azure kernel),
          # no inbound rules, max lifetime 60 min, tagged purpose=vcan-ci, whose user data
          # installs the runner application and runs: ./run.sh --jitconfig "$(cat jit.txt)"
          echo "id=<instance id>" >> "$GITHUB_OUTPUT"

  vcan:
    needs: start
    runs-on: [self-hosted, "${{ needs.start.outputs.label }}"]
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - name: Install
        run: |
          python3.12 -m venv .venv
          .venv/bin/python -m pip install -q -e ".[dev,hardware]"
      - name: Require CAN_ISOTP (a missing module fails here, never skips later)
        run: |
          uname -r
          unshare -r -n bash -euo pipefail -c '
            ip link add dev vcan0 type vcan
            ip link set up vcan0
            .venv/bin/python scripts/probe_can_capabilities.py --require can_isotp_socket_bind
          '
      - name: vcan integration suite (a skip is a failure on this runner)
        run: |
          set -o pipefail
          scripts/run_integration_tests.sh -q -rs --color=no -p no:cacheprovider | tee integration.log
          summary=$(tail -1 integration.log)
          echo "::notice title=vcan integration (disposable VM)::${summary}"
          if grep -qE '[0-9]+ skipped' <<<"$summary"; then
            echo "::error title=unexpected skip::a CAN_ISOTP runner must not skip; see -rs output"
            exit 1
          fi

  stop:
    needs: [start, vcan]
    if: always() && needs.start.outputs.vm != ''
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - run: |
          # PLACEHOLDER: provider CLI. Delete instance ${{ needs.start.outputs.vm }};
          # succeed if it is already gone.
          true
```

`vcan` has no `continue-on-error`, so a failure turns the run red. The existing
informational `can-capabilities` job in the public `ci.yml` stays exactly as it is. The
exact `gh api` fields for JIT configuration must be checked against GitHub's REST
documentation when this is implemented. They are written here from the documented
endpoint, not tested.

## 5. Owner steps, in order, with nothing done by the agent

1. Choose a provider (not Azure; §4.3) and a monthly budget, and set the budget alert
   first.
2. Create the private repository and add it as a second push URL on `origin`.
3. **Measure before building anything.** Boot one VM from the intended image by hand. Log
   in as the unprivileged user and run the two commands from the `vcan` job, plus `sysctl
   kernel.apparmor_restrict_unprivileged_userns` so D3 is a recorded fact. Record the
   provider, image, `uname -r` and the output. Delete the VM. **If the probe fails, stop
   here**; nothing else in this record applies to that provider.
4. Create the two credentials in §4.2 with exactly the scopes listed, as secrets in the
   private repository.
5. Add the workflow, fill in the provider placeholders, push once to `modernization`, and
   confirm in the provider console that the VM was created **and deleted**.
6. Run it on real pushes for a sustained period and record every failure and its cause
   before revisiting §4.4.

## 6. Alternatives considered

None of these is being pursued without a decision.

| Option | Fidelity | Cost | Security |
|---|---|---|---|
| **Agreed direction** (private repository, disposable cloud VM for each run) | Full: a real kernel `CAN_ISOTP` | Pay per run plus a little private-repository Actions time; nothing runs between pushes | Forks can't reach it (§4.1); nothing survives a run |
| Permanent VM host with an overlay VM for each job | Full | A machine that stays on, plus upkeep | Same boundary as above. **Designed only if §5 step 3 or the per-run cost rules out the disposable VM** |
| Self-hosted runner on the public repository with a job guard | Full | Same | **Doesn't meet R4** (§3) |
| QEMU VM inside a hosted runner | Full | Slow boot on every run | Hosted, fine. **Only works if `/dev/kvm` is exposed to the runner, which has to be probed and not assumed** |
| Build `can_isotp` out of tree against `linux-headers-$(uname -r)` | Full once it loads | Breaks whenever the Azure kernel moves | Hosted, fine. Brings back an out-of-tree `.ko` path that Phase 2 removed from the product, for CI only |
| A third, in-process ISO-TP backend | **Changes what a green run means** | Low | Hosted, fine. Not approved |

## 7. Open questions for the owner

1. **Provider and budget.** Both are still to be decided. Azure is excluded by §4.3.
2. How long, and how many clean runs, count as the "reliability and security
   demonstrated" that §4.4 waits for?
