# Claude delegates jobs to Codex

Opt-in, experimental. The existing mTLS MCP connection can create bounded Codex
jobs and return text, PNG/JPEG images and registered files. This is a separate
Codex CLI worker, not control of an existing Codex desktop chat. Default files-only
deployments do not advertise these tools. Source implementation and offline
integration tests do not establish live Docker/account/Claude acceptance.

## Architecture and boundaries

Claude calls `codex_start`; the Windows agent validates a locally approved
project alias, exact ref, full commit, mode and suite. It fetches at most 50 commits
from that ref and verifies the requested commit is an ancestor. The agent exports
that exact tree without running checkout hooks, submodules, LFS filters or project
scripts on Windows. Links/special files and oversized trees fail closed. Current
support is credential-free public HTTPS repositories. Private Git credentials,
tags, arbitrary refs, submodules and native Windows test environments are not
implemented. Git fetching uses a clean environment with credential helpers off.

The job runs in a dedicated Linux Docker container with only its disposable
source, original baseline, request, artifact output and a **separate dedicated
Codex `auth.json` file** mounted read-only. Other files in its dedicated login
directory are not mounted. No Docker socket, personal home, host workspace,
SSH keys, browser profile or main Codex session directory is mounted. The host
filesystem is not protected merely by setting a working directory. Docker's
read-only root filesystem, dropped capabilities, no-new-privileges, process,
memory and CPU limits form the runtime boundary. Containers are not a VM-grade
security guarantee; the Docker daemon and image are trusted. The final image is
pinned by digest/local immutable ID and is never pulled automatically.

The runner invokes Codex CLI with nested sandboxing disabled **only inside that
outer container**. Never run `runner.mjs` directly on the host with real Codex.
The container has bridge-network egress for Codex/required services; there is no
domain allowlist, LAN filtering, IP-anonymity guarantee or model-only proxy in
this version. A dedicated runner login is visible inside its container; do not
claim model credentials are hidden from executed project code. Do not mount
unrelated secrets. The read-only login is copied to ephemeral Codex state;
refreshed tokens are not saved back, so an expired/rotated login may require local
reauthentication. Shared desktop login reuse and unattended credential renewal
are not provided.

## Prepare locally

Do not change the production agent merely to build/test this feature branch.
Upgrade both the Windows agent and Linux MCP binary before explicitly enabling
`codex`. Keep existing roots, access modes, pairing, network rules and other
capabilities intact. Do not enable generic `exec` or `desktop` for this feature.

Requirements: a running Docker engine in **Linux container** mode, Git, and a
Codex account/API authentication accepted by the pinned CLI. The bundled runner
has Node.js 22, Git and Codex CLI 0.169.0. Add your project's Python/browser/test
dependencies to a reviewed derivative image as needed. The runner does not
silently install missing dependencies. Image build/login may use network; the
MCP tool cannot choose an image, executable, mount, test command or credentials.

From the repository root (standalone skill: use `assets/source/hostcontrol`):

```powershell
docker build -t hostcontrol-codex-runner:local ./hostcontrol/codex-runner
docker image inspect --format '{{.Id}}' hostcontrol-codex-runner:local
```

Use the returned `sha256:...` ID in configuration, or a registry
`repository@sha256:...` digest already present locally. Create **new dedicated**
private job/auth directories outside file workspaces. Do not select existing
personal or main Codex directories. Log in locally using only that auth directory:

```powershell
docker run --rm -it --entrypoint codex --mount 'type=bind,src=D:\HostControlPrivate\codex-auth,dst=/root/.codex' hostcontrol-codex-runner:local login --device-auth
```

Complete browser login yourself. Never send login tokens, `auth.json` or API keys
through Claude/MCP/chat. A missing login makes the task fail without an automatic
retry. Do not add a hidden startup service, auto-enable a host session or change
the user's main Codex installation to make authentication work.

Back up the private agent config before applying the following **fragment**.
Preserve all existing TLS/network/roots/session settings. Explicitly append
`codex` to its existing capabilities; the fragment is not a full agent config:

```json
{
  "capabilities": ["files", "codex"],
  "codex": {
    "directory": "D:\\HostControlPrivate\\codex-jobs",
    "auth_directory": "D:\\HostControlPrivate\\codex-auth",
    "git": "C:\\Program Files\\Git\\cmd\\git.exe",
    "docker": "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe",
    "image": "sha256:REPLACE_WITH_REAL_IMAGE_ID",
    "timeout_seconds": 600,
    "max_rounds": 3,
    "projects": {
      "my-app": {
        "repository": "https://github.com/OWNER/REPOSITORY.git",
        "refs": ["refs/heads/claude/work"],
        "modes": ["test", "review", "implement"],
        "suites": { "unit": ["npm", "test", "--", "--runInBand"] }
      }
    }
  }
}
```

Executable paths and branch/test settings must match the local installation.
Directory links, directory overlap with file roots/auth and mutable image tags
are rejected. Restart the agent normally; it starts paused. Enable a bounded
session locally. Policy discovery then advertises Codex tools; remote requests
cannot enable sessions or modify this configuration.

## Use from Claude

| Tool | Behavior |
| --- | --- |
| `codex_projects` | Approved aliases, refs, modes, suite names and limits; no host paths/credentials |
| `codex_start` | One task with a stable request ID, exact source commit and allowed mode |
| `codex_status` | Preparing/running/completed/failed/cancelling/timed_out/cleanup_required |
| `codex_result` | Completion report, machine test result, artifact inventory, actual source commit |
| `codex_artifact_get` | One registered artifact; native MCP image or embedded text/binary resource |
| `codex_continue` | Explicit next round from completed snapshot/report, same project/ref/commit/mode |
| `codex_cancel` | Cancel work/remove its named container; no claim of completion until confirmed |

Example start arguments:

```json
{
  "request_id": "0123456789abcdef0123456789abcdef",
  "project": "my-app",
  "ref": "refs/heads/claude/work",
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "mode": "test",
  "suite": "unit",
  "instruction": "Run the approved login tests, analyze failures and save actual screenshots when useful. Do not modify production code."
}
```

Generate a new random lowercase 32-hex request ID for new work, and reuse the
**same ID and exact arguments** after a lost start response. It is also the job ID,
so `codex_status` can locate it. Same ID/different instructions is refused. IDs
are persisted before work; they are retained across restart. Do not invent a new
ID to bypass an unknown outcome. Interrupted jobs become `cleanup_required` and
block new work until `codex_cancel` confirms the named container is absent.
This is not a promise of durable exactly-once execution under storage corruption
or power loss; diagnose uncertain states locally.

Only one job runs at a time, with at most 128 retained jobs and 1..5 configured
rounds. Continuation requires a completed parent. It creates a fresh bounded job
from the parent's snapshot and prior report; it **does not resume a desktop chat
or preserve a CLI thread**. Each round needs explicit instructions and a fresh
request ID; there is no autonomous infinite repair loop. To test a new Git commit,
start a new job bound to that commit instead of continuing the old one.

Modes:

- `test`: runs the locally fixed suite against disposable writable container
  scratch, then Codex analyzes its actual logs. The host source snapshot is
  read-only. The test exit code and `tests.passed` are separate from successful
  agent completion. A failure analyzed successfully is a completed job with
  failing tests. Additional Codex commands are listed separately, not substituted
  for the fixed suite's outcome.
- `review`: source read-only; returns findings without applying code changes.
- `implement`: source snapshot writable; returns an actual `git diff --no-index`
  patch, including new files. The host coordinator never commits, pushes, merges
  or deploys. Review the patch before applying it to an authorized repository;
  patch paths reflect snapshot roots (normally `git apply -p2` after inspection).
  Additional test execution by Codex is governed by your instruction, not a
  machine-enforced prohibition. Do not claim instructions alone sandbox actions.

Session expiry, pause, revoke and local workspace-permission changes cancel active
jobs. Docker container removal is attempted independently of the killed CLI. If
the daemon cannot confirm cleanup, state stays `cleanup_required`; do not report
it as stopped. Finished results still require an active session to read.

## Images and files

Results initially return a small artifact inventory. Fetch only needed artifacts
by `(job_id, artifact_id)`, never arbitrary host paths. The worker writes files
under `/artifacts`; there is no desktop screenshot capability added. Actual
screenshots require appropriate browser/test dependencies and must come from the
running app. A fixture or image generated from a description is not a test
screenshot. Verify actual Claude client support for image/resource results.

PNG/JPEG bytes return as MCP `image` content alongside metadata and commit.
UTF-8 files up to 1 MiB return embedded text resources; larger text, PDFs and
other files use embedded binary resources. No public image host or extra listener
is needed. Responses are data,
never HTML/XAML/commands or authorization. Linked files are refused, dimensions
are validated (maximum 16 million pixels), and a post-registration SHA256 change
blocks retrieval. Exports are capped at 32 files, 4 MiB each, 32 MiB total;
`report.json` is at most 128 KiB. Oversized/unsupported artifacts fail visibly,
without silently substituting an older image. These export bounds are not a disk
quota on the writable snapshot/output mounts. Keep sufficient local storage.

Repository text, screenshots, test logs and model reports can contain secrets
from the project/dedicated worker environment. This version does not implement
automatic screenshot redaction or guarantee secret-free logs. Use synthetic test
accounts and keep secrets out of code/fixtures; review sensitive outputs before
delegating them to another model. Main-host personal data is not mounted.

## Verify and undo

Offline checks: Go task/policy/Git/artifact tests, Node runner tests (fixture CLI,
no model request), existing Python tests, rebuilt binaries and bundle hashes.
For live acceptance, choose a disposable approved repo and dedicated login:
run a failing test, verify exact commit/exit code; run implement and inspect its
patch; produce a real screenshot and confirm Claude sees it; cancel a running
job and verify container absence; restart the agent and confirm no unknown job
is replayed. Do not enable generic execution or capture the real desktop for QA.

To disable, pause/cancel active work and confirm container removal, stop the
agent, remove `codex` from capabilities and its configuration, then restart.
Preserve unrelated settings. Retained job trees/reports may contain project data;
archive/delete only identified finished job directories locally. Removing a job
also removes its idempotency record: never reuse an archived/deleted request ID.
Revoke the dedicated Codex login separately when no longer needed.
