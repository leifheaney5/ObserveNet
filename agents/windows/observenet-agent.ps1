<#
.SYNOPSIS
    ObserveNet read-only agent for Windows 11 (Windows PowerShell 5.1 or PowerShell 7).

.DESCRIPTION
    Reports what this PC can observe about itself to an ObserveNet backend:

      * NIC receive/transmit throughput (Get-NetAdapterStatistics, counter deltas over a
        monotonic Stopwatch)
      * Windows Firewall profile state (Get-NetFirewallProfile)
      * SMBv1 / SMBv2 server setting (Get-SmbServerConfiguration, registry fallback)
      * Listening TCP sockets and bound UDP endpoints
      * A heartbeat with per-collector capability status

    It is strictly read-only: it changes no setting, sends no probes, scans nothing and
    executes nothing received from the backend. It does not need administrator rights;
    anything it cannot read is reported as "unavailable" together with the reason.

    STATUS: this script was written against the ingest schema in
    backend/observenet/schemas/ingest.py but has NOT been executed on Windows in the
    build environment. Run it with -DryRun first and read the output.

.PARAMETER Url
    Base URL of the ObserveNet backend, e.g. https://observenet.leifslab.com
    Must be https:// unless -AllowLocalHttp is given AND the host is localhost.

.PARAMETER TokenFile
    File containing the collector token (onet_...). The token is never accepted on the
    command line. Restrict the file's ACL (see README).

.PARAMETER StateDir
    Directory for the persisted agent id. Default: %ProgramData%\ObserveNet, falling
    back to %LOCALAPPDATA%\ObserveNet when that is not writable.

.PARAMETER SampleIntervalSeconds
    NIC sampling and upload interval (1-60). Default 5.

.PARAMETER AllowLocalHttp
    Permit plain http:// for a local-only demo. Only honoured for localhost / 127.0.0.1 / ::1.

.PARAMETER AccessHeadersFile
    Optional JSON file with extra request headers, e.g. a Cloudflare Access service
    token: { "CF-Access-Client-Id": "...", "CF-Access-Client-Secret": "..." }

.PARAMETER DryRun
    Collect two NIC readings plus inventory and posture, print the batch as JSON and
    exit without contacting the backend. No token required.

.EXAMPLE
    .\observenet-agent.ps1 -Url https://observenet.leifslab.com -DryRun

.EXAMPLE
    .\observenet-agent.ps1 -Url https://observenet.leifslab.com
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Url,

    [string]$TokenFile = (Join-Path $env:ProgramData 'ObserveNet\agent.token'),

    [string]$StateDir,

    [ValidateRange(1, 60)]
    [int]$SampleIntervalSeconds = 5,

    [switch]$AllowLocalHttp,

    [string]$AccessHeadersFile,

    [switch]$DryRun
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$script:AgentVersion = '0.1.0'
$script:MaxBufferedBatches = 120
$script:MaxGapFactor = 3.0
$script:MaxSockets = 2000
$script:ConfigPollSeconds = 10

# ------------------------------------------------------------------ helpers

function Write-Log {
    param([string]$Level, [string]$Message)
    $stamp = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    Write-Host "$stamp $Level $Message"
}

function Get-UtcStamp {
    return [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
}

function Limit-String {
    param([string]$Value, [int]$Max)
    if ($null -eq $Value) { return $null }
    if ($Value.Length -le $Max) { return $Value }
    return $Value.Substring(0, $Max)
}

function Get-Prop {
    # StrictMode-safe property read for objects returned by ConvertFrom-Json.
    param($Object, [string]$Name, $Default = $null)
    if ($null -eq $Object) { return $Default }
    $p = $Object.PSObject.Properties[$Name]
    if ($null -eq $p -or $null -eq $p.Value) { return $Default }
    return $p.Value
}

function Test-LocalHost {
    param([Uri]$Uri)
    return ($Uri.Host -eq 'localhost' -or $Uri.Host -eq '127.0.0.1' -or $Uri.Host -eq '::1' -or $Uri.Host -eq '[::1]')
}

function Resolve-BaseUrl {
    param([string]$Raw, [bool]$AllowHttp)
    $uri = $null
    if (-not [Uri]::TryCreate($Raw.TrimEnd('/'), [UriKind]::Absolute, [ref]$uri)) {
        throw "Url '$Raw' is not a valid absolute URL."
    }
    if ($uri.Scheme -eq 'https') { return $uri.AbsoluteUri.TrimEnd('/') }
    if ($uri.Scheme -eq 'http' -and $AllowHttp -and (Test-LocalHost -Uri $uri)) {
        return $uri.AbsoluteUri.TrimEnd('/')
    }
    throw ("Refusing to use '$Raw': the agent token is only sent over https://. " +
           "Plain http:// is accepted solely with -AllowLocalHttp and a localhost URL.")
}

function Get-StateDirectory {
    param([string]$Preferred)
    $candidates = @()
    if ($Preferred) { $candidates += $Preferred }
    if ($env:ProgramData) { $candidates += (Join-Path $env:ProgramData 'ObserveNet') }
    if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA 'ObserveNet') }
    foreach ($dir in $candidates) {
        try {
            if (-not (Test-Path -LiteralPath $dir)) {
                New-Item -ItemType Directory -Path $dir -Force | Out-Null
            }
            $probe = Join-Path $dir ('.write-test-' + [Guid]::NewGuid().ToString('N'))
            Set-Content -LiteralPath $probe -Value 'x' -Encoding ASCII
            Remove-Item -LiteralPath $probe -Force
            return $dir
        } catch {
            continue
        }
    }
    return $null
}

function Get-AgentUid {
    # Stable per-install identity: a random id persisted on first run.
    param([string]$Dir)
    if ($Dir) {
        $path = Join-Path $Dir 'agent_uid'
        try {
            if (Test-Path -LiteralPath $path) {
                $value = (Get-Content -LiteralPath $path -Raw).Trim()
                if ($value -match '^[A-Za-z0-9_.:-]{8,64}$') { return $value }
            }
            $value = 'win-' + [Guid]::NewGuid().ToString('N')
            Set-Content -LiteralPath $path -Value $value -Encoding ASCII
            return $value
        } catch {
            Write-Log 'WARN' "cannot persist agent id in $Dir ($($_.Exception.Message)); using a derived id"
        }
    }
    # Fallback: hash of the machine name, so the raw name is not the identity key.
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes('observenet:' + $env:COMPUTERNAME))
    } finally {
        $sha.Dispose()
    }
    return 'win-' + (-join ($bytes[0..15] | ForEach-Object { $_.ToString('x2') }))
}

function Read-Token {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Token file '$Path' not found. Create it with the collector token and restrict its ACL (see README)."
    }
    $token = (Get-Content -LiteralPath $Path -Raw).Trim()
    if ($token.Length -lt 20) { throw "Token file '$Path' does not contain a plausible token." }
    try {
        # Best-effort warning when broad groups can read the token.
        $broad = @('S-1-1-0', 'S-1-5-11', 'S-1-5-32-545')   # Everyone, Authenticated Users, Users
        $acl = Get-Acl -LiteralPath $Path
        foreach ($rule in $acl.Access) {
            if ($rule.AccessControlType -ne 'Allow') { continue }
            try {
                $sid = $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
            } catch {
                continue
            }
            if ($broad -contains $sid) {
                Write-Log 'WARN' "token file is readable by '$($rule.IdentityReference)'; restrict it with icacls (see README)"
                break
            }
        }
    } catch {
        Write-Log 'WARN' "could not inspect the token file ACL: $($_.Exception.Message)"
    }
    return $token
}

function ConvertTo-Mac {
    param([string]$Raw)
    if (-not $Raw) { return $null }
    $mac = $Raw.Trim().ToLowerInvariant().Replace('-', ':')
    if ($mac -match '^[0-9a-f]{2}(:[0-9a-f]{2}){5}$') { return $mac }
    return $null
}

# ------------------------------------------------------------------ collectors

function Get-NicSamples {
    # bits_per_second = 8 * max(0, counter_delta) / elapsed_seconds, elapsed from a Stopwatch.
    # No sample is produced for the first reading, after a counter reset, or after a gap
    # longer than MaxGapFactor intervals: a missing point is a gap, never a guess.
    param([hashtable]$Previous, [double]$NowSeconds, [double]$IntervalSeconds)
    $samples = New-Object System.Collections.ArrayList
    $stats = @(Get-NetAdapterStatistics -ErrorAction Stop)
    $ts = Get-UtcStamp
    $live = @{}
    foreach ($s in $stats) {
        $name = Limit-String -Value ([string]$s.Name) -Max 120
        if (-not $name) { continue }
        $live[$name] = $true
        $rx = [decimal]$s.ReceivedBytes
        $tx = [decimal]$s.SentBytes
        $prev = $null
        if ($Previous.ContainsKey($name)) { $prev = $Previous[$name] }
        $Previous[$name] = @{ Rx = $rx; Tx = $tx; T = $NowSeconds }
        if ($null -eq $prev) { continue }
        $elapsed = $NowSeconds - [double]$prev.T
        if ($elapsed -le 0) { continue }
        if ($elapsed -gt ($IntervalSeconds * $script:MaxGapFactor)) { continue }
        $pairs = @(
            @{ Metric = 'net.rx_bps'; Cur = $rx; Old = [decimal]$prev.Rx },
            @{ Metric = 'net.tx_bps'; Cur = $tx; Old = [decimal]$prev.Tx }
        )
        foreach ($p in $pairs) {
            if ($p.Cur -lt $p.Old) { continue }   # 64-bit counter went backwards: reset, not wrap
            $bps = 8.0 * [double]($p.Cur - $p.Old) / $elapsed
            [void]$samples.Add(@{
                metric     = $p.Metric
                label      = $name
                ts         = $ts
                value      = [Math]::Round($bps, 3)
                unit       = 'bit/s'
                interval_s = [Math]::Round($elapsed, 3)
                quality    = 'measured'
            })
        }
    }
    foreach ($key in @($Previous.Keys)) {
        if (-not $live.ContainsKey($key)) { $Previous.Remove($key) }   # adapter disappeared
    }
    return , $samples
}

function Get-InventorySection {
    $interfaces = New-Object System.Collections.ArrayList
    $adapters = @(Get-NetAdapter -ErrorAction Stop)
    $addresses = @()
    try { $addresses = @(Get-NetIPAddress -ErrorAction Stop) } catch { $addresses = @() }
    foreach ($a in $adapters) {
        $addrList = New-Object System.Collections.ArrayList
        foreach ($ip in $addresses) {
            if ($ip.InterfaceIndex -ne $a.InterfaceIndex) { continue }
            $family = 'ipv4'
            if ([string]$ip.AddressFamily -eq 'IPv6') { $family = 'ipv6' }
            $address = ([string]$ip.IPAddress).Split('%')[0]
            [void]$addrList.Add(@{ family = $family; address = (Limit-String $address 64); prefix_len = [int]$ip.PrefixLength })
        }
        $speed = $null
        try {
            $bits = [double]$a.ReceiveLinkSpeed
            if ($bits -gt 0) { $speed = [int][Math]::Min(1000000, [Math]::Round($bits / 1000000)) }
        } catch { $speed = $null }
        $mtu = $null
        try { if ($a.MtuSize -gt 0 -and $a.MtuSize -le 65536) { $mtu = [int]$a.MtuSize } } catch { $mtu = $null }
        [void]$interfaces.Add(@{
            name            = (Limit-String ([string]$a.Name) 64)
            mac             = (ConvertTo-Mac ([string]$a.MacAddress))
            is_up           = ([string]$a.Status -eq 'Up')
            link_speed_mbps = $speed      # negotiated link rate, NOT throughput
            mtu             = $mtu
            addresses       = @($addrList)
        })
    }
    $gateway = $null
    try {
        $route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction Stop |
            Sort-Object -Property @{ Expression = { $_.RouteMetric + $_.InterfaceMetric } } |
            Select-Object -First 1
        if ($route -and $route.NextHop -and $route.NextHop -ne '0.0.0.0') {
            $gateway = @{ ip = [string]$route.NextHop; interface = (Limit-String ([string]$route.InterfaceAlias) 64) }
        }
    } catch { $gateway = $null }
    return @{
        collected_at     = (Get-UtcStamp)
        interfaces       = @($interfaces)
        default_gateway  = $gateway
        neighbors_status = 'unsupported'   # this agent deliberately reports no neighbour table
        neighbors        = @()
    }
}

function Get-FirewallSection {
    $profiles = $null
    $method = ''
    try {
        $profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore -ErrorAction Stop)
        $method = 'Get-NetFirewallProfile -PolicyStore ActiveStore (effective policy)'
    } catch {
        try {
            $profiles = @(Get-NetFirewallProfile -ErrorAction Stop)
            $method = 'Get-NetFirewallProfile (local policy store)'
        } catch {
            return @{ status = 'unavailable'; reason = (Limit-String $_.Exception.Message 300); method = ''; profiles = @() }
        }
    }
    $out = New-Object System.Collections.ArrayList
    foreach ($p in $profiles) {
        $name = [string]$p.Name
        if (@('Domain', 'Private', 'Public') -notcontains $name) { continue }
        $state = [string]$p.Enabled
        if ($state -ne 'True' -and $state -ne 'False') {
            return @{
                status = 'unavailable'
                reason = "profile '$name' reports Enabled=$state; the effective state could not be read"
                method = $method; profiles = @()
            }
        }
        [void]$out.Add(@{ name = $name; enabled = ($state -eq 'True') })
    }
    if ($out.Count -eq 0) {
        return @{ status = 'unavailable'; reason = 'no firewall profiles returned'; method = $method; profiles = @() }
    }
    return @{ status = 'ok'; reason = $null; method = $method; profiles = @($out) }
}

function Get-SmbSection {
    try {
        $cfg = Get-SmbServerConfiguration -ErrorAction Stop
        return @{
            status = 'ok'; reason = $null; method = 'Get-SmbServerConfiguration'
            smb1_enabled = [bool]$cfg.EnableSMB1Protocol
            smb2_enabled = [bool]$cfg.EnableSMB2Protocol
        }
    } catch {
        $cmdletError = $_.Exception.Message
    }
    # Fallback readable without elevation. An absent value means "OS default", which this
    # agent cannot resolve without elevation, so it is reported as null (unknown), not guessed.
    try {
        $key = 'HKLM:\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters'
        $item = Get-ItemProperty -LiteralPath $key -ErrorAction Stop
        $smb1 = $null
        $smb2 = $null
        if ($item.PSObject.Properties['SMB1']) { $smb1 = ([int]$item.SMB1 -ne 0) }
        if ($item.PSObject.Properties['SMB2']) { $smb2 = ([int]$item.SMB2 -ne 0) }
        $reason = $null
        if ($null -eq $smb1) {
            $reason = 'SMB1 registry value is not set (OS default applies); Get-SmbServerConfiguration needs elevation: ' + $cmdletError
        }
        return @{
            status = 'ok'; reason = (Limit-String $reason 300)
            method = 'registry: LanmanServer\Parameters (SMB1, SMB2)'
            smb1_enabled = $smb1; smb2_enabled = $smb2
        }
    } catch {
        return @{
            status = 'unavailable'
            reason = (Limit-String ("Get-SmbServerConfiguration: $cmdletError; registry: " + $_.Exception.Message) 300)
            method = ''; smb1_enabled = $null; smb2_enabled = $null
        }
    }
}

function Get-ListeningSection {
    $names = @{}
    try {
        foreach ($proc in @(Get-Process -ErrorAction SilentlyContinue)) { $names[[int]$proc.Id] = [string]$proc.ProcessName }
    } catch { $names = @{} }

    $seen = @{}
    $items = New-Object System.Collections.ArrayList
    $errors = @()
    $add = {
        param($Proto, $Address, $Port, $ProcessId)
        $address = ([string]$Address).Split('%')[0]
        $port = [int]$Port
        if ($port -lt 1 -or $port -gt 65535 -or -not $address) { return }
        $key = "$Proto|$address|$port"
        if ($seen.ContainsKey($key) -or $items.Count -ge $script:MaxSockets) { return }
        $seen[$key] = $true
        $process = $null
        if ($null -ne $ProcessId -and $names.ContainsKey([int]$ProcessId)) { $process = Limit-String $names[[int]$ProcessId] 120 }
        [void]$items.Add(@{ proto = $Proto; address = (Limit-String $address 64); port = $port; process = $process })
    }
    try {
        foreach ($c in @(Get-NetTCPConnection -State Listen -ErrorAction Stop)) {
            & $add 'tcp' $c.LocalAddress $c.LocalPort $c.OwningProcess
        }
    } catch { $errors += ('tcp: ' + $_.Exception.Message) }
    try {
        foreach ($u in @(Get-NetUDPEndpoint -ErrorAction Stop)) {
            & $add 'udp' $u.LocalAddress $u.LocalPort $u.OwningProcess
        }
    } catch { $errors += ('udp: ' + $_.Exception.Message) }

    if ($errors.Count -ge 2) {
        return @{ status = 'unavailable'; reason = (Limit-String ($errors -join '; ') 300); method = ''; items = @() }
    }
    $reason = $null
    if ($errors.Count -eq 1) { $reason = Limit-String ('partial: ' + $errors[0]) 300 }
    return @{
        status = 'ok'; reason = $reason
        method = 'Get-NetTCPConnection -State Listen; Get-NetUDPEndpoint'
        items  = @($items)
    }
}

function Get-MemoryPercent {
    try {
        $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
        if ($os.TotalVisibleMemorySize -gt 0) {
            return [Math]::Round(100.0 * ($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / $os.TotalVisibleMemorySize, 1)
        }
    } catch { }
    return $null
}

# ------------------------------------------------------------------ transport

function Invoke-Api {
    param([string]$Method, [string]$Path, [string]$Body)
    $params = @{
        Uri             = ($script:BaseUrl + $Path)
        Method          = $Method
        Headers         = $script:Headers
        UserAgent       = "observenet-agent-windows/$($script:AgentVersion)"
        TimeoutSec      = 10
        UseBasicParsing = $true
        ErrorAction     = 'Stop'
    }
    if ($Body) {
        $params['Body'] = [Text.Encoding]::UTF8.GetBytes($Body)
        $params['ContentType'] = 'application/json; charset=utf-8'
    }
    try {
        $resp = Invoke-WebRequest @params
        return @{ Status = [int]$resp.StatusCode; Content = [string]$resp.Content; Error = $null }
    } catch {
        $status = 0
        $prop = $_.Exception.PSObject.Properties['Response']
        if ($prop -and $prop.Value) {
            try { $status = [int]$prop.Value.StatusCode } catch { $status = 0 }
        }
        return @{ Status = $status; Content = ''; Error = $_.Exception.Message }
    }
}

function Send-Buffer {
    # Oldest first, same batch_id on every retry: the backend applies each batch at most once.
    param([System.Collections.Generic.Queue[string]]$Buffer)
    while ($Buffer.Count -gt 0) {
        $result = Invoke-Api -Method 'POST' -Path '/api/v1/ingest/agent' -Body $Buffer.Peek()
        $status = $result.Status
        if ($status -eq 200 -or $status -eq 202) {
            [void]$Buffer.Dequeue()
            continue
        }
        if ($status -eq 0 -or $status -eq 429 -or $status -ge 500) {
            Write-Log 'WARN' "upload deferred (status $status, $($result.Error)); $($Buffer.Count) batch(es) buffered"
            return
        }
        # Other 4xx: retrying the same payload cannot succeed.
        Write-Log 'ERROR' "batch rejected with status ${status}: $($result.Error)"
        [void]$Buffer.Dequeue()
        if ($status -eq 401 -or $status -eq 403) {
            $Buffer.Clear()
            Start-Sleep -Seconds 30
            return
        }
    }
}

function Update-Config {
    $result = Invoke-Api -Method 'GET' -Path '/api/v1/agent/config' -Body $null
    if ($result.Status -ne 200) { return }
    try {
        $cfg = $result.Content | ConvertFrom-Json
    } catch {
        return
    }
    $script:PostureIntervalS = [double](Get-Prop $cfg 'posture_interval_s' 300)
    $script:InventoryIntervalS = [Math]::Max(30.0, [double](Get-Prop $cfg 'inventory_interval_s' 60))
    $requested = Get-Prop $cfg 'posture_requested_at' $null
    if ($null -ne $requested) {
        $requested = [string]$requested
        if ($requested -ne $script:PostureServed) {
            # The backend asked for fresh evidence (a "Verify fix" request).
            $script:PostureServed = $requested
            $script:PostureDue = 0.0
        }
    }
}

# ------------------------------------------------------------------ batch assembly

function New-Batch {
    param(
        [System.Collections.ArrayList]$Samples,
        $Inventory,
        $Posture,
        [hashtable]$Capabilities,
        [hashtable]$Errors,
        [double]$UptimeSeconds,
        $MemoryPercent
    )
    $caps = New-Object System.Collections.ArrayList
    foreach ($name in ($Capabilities.Keys | Sort-Object)) {
        $c = $Capabilities[$name]
        [void]$caps.Add(@{ name = $name; status = $c.status; detail = (Limit-String $c.detail 500) })
    }
    $errs = @{}
    foreach ($name in $Errors.Keys) { $errs[(Limit-String $name 64)] = (Limit-String ([string]$Errors[$name]) 500) }
    $now = Get-UtcStamp
    $batch = @{
        schema_version = 1
        agent          = @{
            agent_uid     = $script:AgentUid
            hostname      = (Limit-String $env:COMPUTERNAME 255)
            platform      = 'windows'
            agent_version = $script:AgentVersion
        }
        batch_id       = [Guid]::NewGuid().ToString('N')
        sent_at        = $now
        samples        = @($Samples)
        probe_results  = @()          # this agent runs no probes
        heartbeat      = @{
            ts           = $now
            cpu_pct      = $null
            mem_pct      = $MemoryPercent
            uptime_s     = [Math]::Round($UptimeSeconds, 1)
            errors       = $errs
            capabilities = @($caps)
        }
    }
    if ($null -ne $Inventory) { $batch['inventory'] = $Inventory }
    if ($null -ne $Posture) { $batch['posture'] = $Posture }
    return (ConvertTo-Json -InputObject $batch -Depth 8 -Compress)
}

function Get-PostureSection {
    param([hashtable]$Capabilities, [hashtable]$Errors)
    $firewall = Get-FirewallSection
    $smb = Get-SmbSection
    $listening = Get-ListeningSection
    $map = @(
        @{ Name = 'windows_firewall'; Section = $firewall },
        @{ Name = 'smb_configuration'; Section = $smb },
        @{ Name = 'listening_sockets'; Section = $listening }
    )
    foreach ($m in $map) {
        if ($m.Section.status -eq 'ok') {
            $Capabilities[$m.Name] = @{ status = 'supported'; detail = [string]$m.Section.method }
            $Errors.Remove($m.Name)
        } else {
            $Capabilities[$m.Name] = @{ status = 'unavailable'; detail = [string]$m.Section.reason }
            $Errors[$m.Name] = [string]$m.Section.reason
        }
    }
    foreach ($section in @($firewall, $smb, $listening)) {
        $section['method'] = Limit-String ([string]$section['method']) 120   # schema: string, never null
    }
    return @{ collected_at = (Get-UtcStamp); firewall = $firewall; smb = $smb; listening = $listening }
}

# ------------------------------------------------------------------ main

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

$script:BaseUrl = Resolve-BaseUrl -Raw $Url -AllowHttp ([bool]$AllowLocalHttp)
$script:AgentUid = Get-AgentUid -Dir (Get-StateDirectory -Preferred $StateDir)
$script:Headers = @{}
$script:PostureIntervalS = 300.0
$script:InventoryIntervalS = 60.0
$script:PostureServed = $null
$script:PostureDue = 0.0

if (-not $DryRun) {
    $script:Headers['Authorization'] = 'Bearer ' + (Read-Token -Path $TokenFile)
    if ($AccessHeadersFile) {
        $extra = Get-Content -LiteralPath $AccessHeadersFile -Raw | ConvertFrom-Json
        foreach ($p in $extra.PSObject.Properties) { $script:Headers[[string]$p.Name] = [string]$p.Value }
    }
}

$clock = [System.Diagnostics.Stopwatch]::StartNew()
$previous = @{}
$capabilities = @{}
$errors = @{}
$buffer = New-Object 'System.Collections.Generic.Queue[string]'
$inventoryDue = 0.0
$configDue = 0.0
$memoryPercent = $null
$interval = [double]$SampleIntervalSeconds

Write-Log 'INFO' "observenet agent $($script:AgentVersion) starting; uid=$($script:AgentUid) backend=$($script:BaseUrl) dryrun=$([bool]$DryRun)"

if ($DryRun) {
    try { [void](Get-NicSamples -Previous $previous -NowSeconds $clock.Elapsed.TotalSeconds -IntervalSeconds $interval) } catch { $errors['nic_counters'] = $_.Exception.Message }
    Start-Sleep -Seconds $SampleIntervalSeconds
}

while ($true) {
    $loopStart = $clock.Elapsed.TotalSeconds

    $samples = New-Object System.Collections.ArrayList
    try {
        $samples = Get-NicSamples -Previous $previous -NowSeconds $loopStart -IntervalSeconds $interval
        $capabilities['nic_counters'] = @{ status = 'supported'; detail = "Get-NetAdapterStatistics cumulative counters, $SampleIntervalSeconds s deltas" }
        $errors.Remove('nic_counters')
    } catch {
        $capabilities['nic_counters'] = @{ status = 'unavailable'; detail = $_.Exception.Message }
        $errors['nic_counters'] = $_.Exception.Message
    }

    if (-not $DryRun -and $loopStart -ge $configDue) {
        $configDue = $loopStart + $script:ConfigPollSeconds
        Update-Config
    }

    $inventory = $null
    if ($loopStart -ge $inventoryDue) {
        $inventoryDue = $loopStart + $script:InventoryIntervalS
        $memoryPercent = Get-MemoryPercent
        try {
            $inventory = Get-InventorySection
            $errors.Remove('inventory')
        } catch {
            $errors['inventory'] = $_.Exception.Message
        }
    }

    $posture = $null
    if ($loopStart -ge $script:PostureDue) {
        $script:PostureDue = $loopStart + $script:PostureIntervalS
        try {
            $posture = Get-PostureSection -Capabilities $capabilities -Errors $errors
        } catch {
            $errors['posture'] = $_.Exception.Message
        }
    }

    $json = New-Batch -Samples $samples -Inventory $inventory -Posture $posture -Capabilities $capabilities `
        -Errors $errors -UptimeSeconds $clock.Elapsed.TotalSeconds -MemoryPercent $memoryPercent

    if ($DryRun) {
        Write-Output $json
        break
    }

    if ($buffer.Count -ge $script:MaxBufferedBatches) {
        [void]$buffer.Dequeue()
        $errors['upload_buffer'] = 'buffer full; oldest batch dropped'
    }
    $buffer.Enqueue($json)
    Send-Buffer -Buffer $buffer

    $remaining = $interval - ($clock.Elapsed.TotalSeconds - $loopStart)
    if ($remaining -gt 0) { Start-Sleep -Milliseconds ([int]($remaining * 1000)) }
}
