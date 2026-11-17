# ObserveNet Windows agent (optional)

> **Untested.** This script was written against the backend's ingest schema but has
> **never been executed or even syntax-checked by a PowerShell interpreter**: the build
> environment had no Windows host and no PowerShell. The backend side (schema,
> Windows rules, verification) is exercised with fixture payloads only. Expect to fix
> small issues on first run. Start with `-DryRun`.

`observenet-agent.ps1` is a read-only PowerShell agent for Windows 11 (Windows
PowerShell 5.1 or PowerShell 7). It reports what the PC can observe about itself.

## What it reports

| Data | Cmdlet | Notes |
|---|---|---|
| NIC throughput `net.rx_bps` / `net.tx_bps` per adapter | `Get-NetAdapterStatistics` | Counter deltas over a monotonic `Stopwatch`; default every 5 s. First reading, counter reset and gaps > 3 intervals produce no sample. |
| Adapters, MAC, addresses, link speed, default route | `Get-NetAdapter`, `Get-NetIPAddress`, `Get-NetRoute` | Link speed is the negotiated rate, not throughput. |
| Firewall profile state (Domain / Private / Public) | `Get-NetFirewallProfile -PolicyStore ActiveStore`, falling back to the local store | If the state is `NotConfigured` in the fallback store the section is `unavailable` rather than guessed. |
| SMBv1 / SMBv2 server setting | `Get-SmbServerConfiguration`; fallback: registry `HKLM:\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters` (`SMB1`, `SMB2`) | The cmdlet normally needs elevation. Without it, an explicit registry value is used; if the value is absent the setting is reported as unknown (`null`), and the backend check result is `unknown`. |
| Listening TCP sockets, bound UDP endpoints | `Get-NetTCPConnection -State Listen`, `Get-NetUDPEndpoint` | Process names via `Get-Process` where permitted. Capped at 2000 entries. |
| Heartbeat | — | Memory %, agent uptime, per-collector errors, capabilities `nic_counters`, `windows_firewall`, `smb_configuration`, `listening_sockets`. |

What it does **not** do: no probes, no pings, no scanning, no neighbour/ARP table, no
changes to any setting, no command execution from the backend. It needs no
administrator rights; sections it cannot read are sent as `unavailable` with the reason.

Backend rules that use this data: `WIN-FW-001` (firewall profile disabled),
`WIN-SMB-001` (SMBv1 enabled), `WIN-LISTEN-001` (wildcard listeners not on the
allowlist). Note that Windows normally has many wildcard listeners (RPC, SMB, mDNS …);
expect several low-severity `WIN-LISTEN-001` findings until you add intended ones to the
listener allowlist in Settings.

## Requirements

- Windows 11 (the `NetAdapter`, `NetTCPIP`, `NetSecurity` and `SmbShare` modules ship
  with it).
- An **HTTPS** URL for the backend. The agent refuses plain HTTP except with
  `-AllowLocalHttp` **and** a `localhost` URL (useful only when the backend runs on the
  same PC). For a PC on the LAN this means the public hostname
  (`https://observenet.leafslab.com`) must be online, or you must provide a TLS endpoint
  on the LAN yourself.
- A collector token created for this PC.
- The PC's clock must be within 120 s of the server's, or batches are rejected (422).

If the hostname is protected by Cloudflare Access, the agent cannot complete an
interactive login. Either add an Access **service token** policy for the paths
`/api/v1/ingest/agent` and `/api/v1/agent/config` and pass the two headers through
`-AccessHeadersFile`, or add a bypass policy for exactly those two paths (they are still
protected by the collector token). See `docs/DOMAIN_AND_AUTH.md`.

## 1. Dry run (no backend, no token)

Prints one batch as JSON after two NIC readings and exits:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\observenet-agent.ps1 -Url https://observenet.leafslab.com -DryRun
```

Check that `samples`, `inventory` and `posture` look right and that every section has a
`status`. `sample-payload.json` in this directory shows the expected shape; that file
(hand-written, not captured from a real PC) validates against the backend's
`IngestPayload` schema.

## 2. Create a token

In ObserveNet: Settings → Agent tokens → New token (name it after the PC), or on the server:

```bash
docker compose exec api python -m observenet.cli create-agent-token --name windows-pc --ttl-days 90
```

The token is shown once. It becomes bound to this PC's agent id on first use.

## 3. Store the token with a restricted ACL

Run in an elevated PowerShell (only this setup step needs elevation):

```powershell
New-Item -ItemType Directory -Force -Path "$env:ProgramData\ObserveNet" | Out-Null
```

```powershell
Read-Host -Prompt 'Paste the collector token' | Set-Content -Path "$env:ProgramData\ObserveNet\agent.token" -Encoding ASCII -NoNewline
```

```powershell
icacls "$env:ProgramData\ObserveNet\agent.token" /inheritance:r /grant:r "$($env:USERNAME):(R)" "SYSTEM:(R)" "Administrators:(F)"
```

Replace `$env:USERNAME` with the account the scheduled task will run as if it differs.
The token is never passed on the command line (command lines are visible to other
processes). The agent warns at startup if broad groups can read the file.

Optional, for Cloudflare Access service tokens — a second file with the same ACL:

```json
{ "CF-Access-Client-Id": "xxxxxxxx.access", "CF-Access-Client-Secret": "xxxxxxxx" }
```

## 4. Run it in the foreground once

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\ObserveNet\observenet-agent.ps1 -Url https://observenet.leafslab.com
```

Within a few seconds the PC should appear under Settings → Data sources and Devices.
Stop with Ctrl-C.

## 5. Register a scheduled task (run at logon, restart on failure)

As the user who should run it (no elevation needed for a per-user task):

```powershell
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\ObserveNet\observenet-agent.ps1" -Url https://observenet.leafslab.com'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'ObserveNet Agent' -Action $action -Trigger $trigger -Settings $settings -Description 'ObserveNet read-only agent'
```

```powershell
Start-ScheduledTask -TaskName 'ObserveNet Agent'
```

To run before logon, register the task for an account with "Run whether user is logged
on or not" (this stores that account's credentials in Task Scheduler) or as `SYSTEM`
with `-RunLevel` left at the default. Running as `SYSTEM` gives full visibility
(including `Get-SmbServerConfiguration`) at the cost of running a script with high
privileges; prefer a standard user unless you need the SMB reading.

Remove:

```powershell
Unregister-ScheduledTask -TaskName 'ObserveNet Agent' -Confirm:$false
```

## Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `-Url` | required | Backend base URL (HTTPS) |
| `-TokenFile` | `%ProgramData%\ObserveNet\agent.token` | File with the collector token |
| `-StateDir` | `%ProgramData%\ObserveNet`, else `%LOCALAPPDATA%\ObserveNet` | Where the random agent id is stored |
| `-SampleIntervalSeconds` | 5 | NIC sampling / upload interval (1–60) |
| `-AllowLocalHttp` | off | Allow `http://localhost…` only |
| `-AccessHeadersFile` | — | JSON file with extra request headers |
| `-DryRun` | off | Print one batch and exit |

## Behaviour details

- **Config polling:** every 10 s the agent reads `/api/v1/agent/config` for posture and
  inventory intervals. When `posture_requested_at` changes (someone pressed *Verify fix*)
  it collects a fresh posture snapshot on the next cycle.
- **Retries:** batches are kept in a bounded in-memory queue (120) and retried oldest
  first with the same `batch_id`; the backend applies each batch at most once. 4xx
  responses other than 429 drop the batch; 401/403 clear the queue and pause 30 s.
- **Identity:** a random `win-…` id persisted in the state directory.
- **Probe targets** in the config are ignored: this agent runs none.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Refusing to use 'http://…'` | Use the HTTPS hostname, or `-AllowLocalHttp` with localhost only |
| `batch rejected with status 401` | Token wrong, expired or revoked |
| `status 403` | Token already bound to another agent id (the state directory changed) — issue a new token |
| `status 409` | The server runs in `DEMO_MODE` and rejects real agents |
| `status 422` | Clock skew > 120 s, or a schema mismatch (please report the message) |
| `status 0` with an HTML/redirect error | An edge access policy is intercepting the request; see the Cloudflare Access note above |
| SMB section `unknown` | Not elevated and the `SMB1` registry value is not set; run once elevated to read it, or accept `unknown` |
