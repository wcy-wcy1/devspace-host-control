param([string]$KeyPath, [switch]$FunctionsOnly)
$ErrorActionPreference = 'Stop'
function Get-DraftHash([string]$Text) {
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}
function Normalize-Draft([string]$Text) { return $Text.Replace("`r`n","`n").Replace("`r","`n") }
function New-BridgePacket([byte[]]$Key, [string]$Id, [string]$Text, [string]$Stream='', [int]$Sequence=0, [string]$Base='') {
    $Text=Normalize-Draft $Text
    $sync=$Stream -ne ''
    if ((-not $sync -and [string]::IsNullOrWhiteSpace($Text)) -or ([regex]::Matches($Text,'[\uD800-\uDBFF][\uDC00-\uDFFF]|[\s\S]')).Count -gt 4000 -or $Text -match '[\x00-\x08\x0B\x0C\x0E-\x1F]') { throw '请输入有效文字（最多 4000 个字符）。' }
    $p=@{kind='draft';id=$Id;at=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds();text=$Text}
    $prefix='HC-DRAFT/1 '
    if ($sync) {
        if ($Stream -cnotmatch '^[a-f0-9]{32}$' -or $Sequence -lt 1 -or $Base -cnotmatch '^[a-f0-9]{64}$') { throw '同步版本无效。' }
        $p.kind='sync'; $p.stream=$Stream; $p.seq=$Sequence; $p.base=$Base; $prefix='HC-DRAFT/2 '
    }
    $body=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($p | ConvertTo-Json -Compress)))
    $hmac=New-Object Security.Cryptography.HMACSHA256
    try { $hmac.Key=$Key; $mac=([BitConverter]::ToString($hmac.ComputeHash([Text.Encoding]::ASCII.GetBytes($body)))).Replace('-','').ToLowerInvariant() } finally { $hmac.Dispose() }
    $envelope=@{body=$body;mac=$mac} | ConvertTo-Json -Compress
    return $prefix+[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($envelope))
}
function Read-BridgeResult([byte[]]$Key, [string]$Id, [string]$Raw, [switch]$Sync) {
    $prefix=if ($Sync) { 'HC-DRAFT/2 ' } else { 'HC-DRAFT/1 ' }
    if (-not $Raw.StartsWith($prefix) -or $Raw.Length -gt 65536) { return $null }
    try {
        $outer=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Raw.Substring(11))) | ConvertFrom-Json
        $hmac=New-Object Security.Cryptography.HMACSHA256
        try { $hmac.Key=$Key; $mac=([BitConverter]::ToString($hmac.ComputeHash([Text.Encoding]::ASCII.GetBytes($outer.body)))).Replace('-','').ToLowerInvariant() } finally { $hmac.Dispose() }
        if ($mac -cne $outer.mac) { return $null }
        $p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($outer.body)) | ConvertFrom-Json
        $age=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()-[long]$p.at
        $kind=if ($Sync) { 'sync_result' } else { 'result' }
        $allowed=if ($Sync) { @('synced','conflict','not_ready','uncertain') } else { @('filled','not_ready','has_draft','uncertain') }
        if ($p.kind -ne $kind -or $p.id -cne $Id -or $age -lt -2 -or $age -gt 20 -or $p.status -notin $allowed) { return $null }
        return $p.status
    } catch { return $null }
}
function Get-SyncDecision($State, [string]$Text, [DateTime]$Now) {
    if (-not $State.enabled -or $State.paused -or $State.composing -or $null -ne $State.pending) { return 'wait' }
    if ((Normalize-Draft $Text) -ceq $State.confirmed) { return 'wait' }
    if (($Now-$State.changed).TotalMilliseconds -lt 600) { return 'wait' }
    return 'send'
}
function Test-OtherClipboard($State, [string]$Raw) {
    # RDP can briefly expose no text, or echo a preceding clipboard revision.
    # Recognize our own recent values locally without retransmitting them.
    if ($Raw -eq '' -or $Raw.StartsWith('HC-DRAFT/2 ')) { return $false }
    return (Get-DraftHash (Normalize-Draft $Raw)) -notin $State.clipHashes
}
if ($FunctionsOnly) { return }
Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Security
$panelMutex=New-Object Threading.Mutex($false,'Local\HostControl-Claude-Draft-Panel')
$panelAcquired=$false
try { $panelAcquired=$panelMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $panelAcquired=$true }
if (-not $panelAcquired) { $panelMutex.Dispose(); throw '本地输入框已经打开，请使用已有窗口。' }
if (-not $KeyPath -or -not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) { throw '尚未配对本地输入框，请先完成输入桥接配置。' }
$key=[Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes($KeyPath),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
if ($key.Length -ne 32) { throw '输入框配对无效。' }
[xml]$xaml=@'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Title="Claude 本地输入框" Width="740" Height="580" MinWidth="580" MinHeight="480" Background="#F7FAFD" FontFamily="Microsoft YaHei UI" WindowStartupLocation="CenterScreen">
<Grid Margin="30">
<Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
<TextBlock Text="本地输入，实时同步" FontSize="28" FontWeight="SemiBold" Foreground="#173544"/>
<TextBlock Grid.Row="1" Text="暂停输入约 0.6 秒后更新远程草稿；检查后在 Claude 中发送。" TextWrapping="Wrap" Foreground="#586F7F" Margin="0,12,0,20" FontSize="14"/>
<TextBox Name="Draft" Grid.Row="2" AcceptsReturn="True" AcceptsTab="True" TextWrapping="Wrap" VerticalScrollBarVisibility="Auto" Padding="16" FontSize="17" Background="White" BorderBrush="#BDCCDA" BorderThickness="1" AutomationProperties.Name="实时同步到远程 Claude 的草稿"/>
<DockPanel Grid.Row="3" Margin="0,18,0,0" LastChildFill="False">
<Button Name="Resume" DockPanel.Dock="Right" Content="重新连接空草稿" Background="#2468A0" Foreground="White" Padding="16,11" FontSize="14" BorderThickness="0"/>
<CheckBox Name="Live" IsChecked="True" Content="实时同步" VerticalAlignment="Center" Foreground="#173544" FontSize="15" Margin="0,0,20,0"/>
</DockPanel>
<TextBlock Name="Status" Grid.Row="4" Text="请保持远程桌面连接，Claude 消息框先留空。同步期间使用剪贴板，暂停后恢复。" TextWrapping="Wrap" Foreground="#586F7F" Margin="0,18,0,0" FontSize="13"/>
</Grid></Window>
'@
$reader=New-Object Xml.XmlNodeReader $xaml
$window=[Windows.Markup.XamlReader]::Load($reader)
$draft=$window.FindName('Draft'); $live=$window.FindName('Live'); $resume=$window.FindName('Resume'); $status=$window.FindName('Status')
$script:sync=@{stream=[Guid]::NewGuid().ToString('N');seq=0;confirmed='';pending=$null;clipHashes=@();held=$false;backup=$null;expected='';enabled=$true;paused=$false;composing=$false;changed=[DateTime]::UtcNow}
function Restore-BridgeClipboard([string]$Expected) {
    try {
        if ([System.Windows.Forms.Clipboard]::ContainsText() -and [System.Windows.Forms.Clipboard]::GetText() -ceq $Expected) {
            if ($null -ne $script:sync.backup) { [System.Windows.Forms.Clipboard]::SetDataObject($script:sync.backup,$true,2,50) }
            else { [System.Windows.Forms.Clipboard]::Clear() }
        }
        $script:sync.held=$false; $script:sync.backup=$null
    } catch {} # Preserve newer clipboard contents.
}
function Stop-DraftSync([string]$Message) {
    $script:sync.paused=$true
    $status.Text=$Message+' 本地文字已保留；处理远端草稿后点击「重新连接空草稿」。'
}
function Start-DraftSync {
    try {
        $text=Normalize-Draft $draft.Text
        $id=[Guid]::NewGuid().ToString('N')
        $sequence=$script:sync.seq+1
        $raw=New-BridgePacket $key $id $text $script:sync.stream $sequence (Get-DraftHash $script:sync.confirmed)
        # Snapshot locally so replacing the clipboard does not invalidate it.
        # Only the signed draft packet is transmitted to the bridge.
        if (-not $script:sync.held) {
            $original=[System.Windows.Forms.Clipboard]::GetDataObject()
            $saved=New-Object System.Windows.Forms.DataObject
            if ($null -ne $original) {
                foreach ($format in $original.GetFormats($false)) {
                    try {
                        $value=$original.GetData($format,$false)
                        $saved.SetData($format,$value)
                        if ($format -in @('Text','UnicodeText') -and $value -is [string]) {
                            $script:sync.clipHashes+=Get-DraftHash (Normalize-Draft $value)
                        }
                    }
                    catch { throw '无法备份剪贴板，请先完成其它复制操作。' }
                }
            }
            $script:sync.backup=$saved; $script:sync.held=$true
        }
        $script:sync.clipHashes=@($script:sync.clipHashes | Select-Object -Last 30)+(Get-DraftHash $text)+(Get-DraftHash $script:sync.confirmed)
        $script:sync.pending=@{id=$id;raw=$raw;text=$text;started=[DateTime]::UtcNow}
        $script:sync.expected=$raw
        $transfer=New-Object System.Windows.Forms.DataObject
        $transfer.SetText($raw)
        [System.Windows.Forms.Clipboard]::SetDataObject($transfer,$true,2,50)
        $script:sync.seq=$sequence
        $resume.IsEnabled=$false
        $status.Text='正在同步… 可以继续输入，下一次只同步最新内容。'
    } catch {
        $script:sync.pending=$null; $resume.IsEnabled=$true
        Stop-DraftSync ('未开始同步：'+$_.Exception.Message)
    }
}
$timer=New-Object Windows.Threading.DispatcherTimer
$timer.Interval=[TimeSpan]::FromMilliseconds(100)
$timer.Add_Tick({
    try {
        $pending=$script:sync.pending
        if ($null -ne $pending) {
            $raw=if ([System.Windows.Forms.Clipboard]::ContainsText()) { [System.Windows.Forms.Clipboard]::GetText() } else { '' }
            $answer=Read-BridgeResult $key $pending.id $raw -Sync
            if ($answer) {
                $script:sync.expected=$raw
                # Keep ownership through consecutive updates. Restoring between
                # packets races RDP's asynchronous clipboard announcements.
                if ($answer -ne 'synced' -or -not $script:sync.enabled -or $script:sync.paused) { Restore-BridgeClipboard $raw }
                $script:sync.pending=$null; $resume.IsEnabled=$true
                if ($answer -eq 'synced') {
                    $script:sync.confirmed=$pending.text
                    if ($script:sync.paused -or -not $script:sync.enabled) { $status.Text='同步已暂停，已发出的更新已确认。' }
                    elseif ((Normalize-Draft $draft.Text) -ceq $pending.text) { $status.Text='已同步到 Claude 草稿。请在 Claude 中检查并发送。' }
                    else { $status.Text='正在等待同步最新输入…' }
                } else {
                    $messages=@{not_ready='未找到可用的 Claude 输入框。';conflict='远端草稿或对话发生变化，同步已暂停。';uncertain='无法确认远端更新结果，同步已暂停。'}
                    Stop-DraftSync $messages[$answer]
                }
            } elseif (([DateTime]::UtcNow-$pending.started).TotalSeconds -ge 18) {
                Restore-BridgeClipboard $pending.raw
                $script:sync.pending=$null; $resume.IsEnabled=$true
                Stop-DraftSync '未收到确认；请检查远程连接与 Claude 草稿，同步不会自动重试。'
            } elseif ($raw -cne $pending.raw -and (Test-OtherClipboard $script:sync $raw)) {
                $script:sync.held=$false; $script:sync.backup=$null
                $script:sync.pending=$null; $resume.IsEnabled=$true
                Stop-DraftSync '检测到其它剪贴板操作，同步已暂停；远端结果请人工确认。'
            }
        }
        if ($script:sync.held -and $null -eq $script:sync.pending) {
            $idleRaw=if ([System.Windows.Forms.Clipboard]::ContainsText()) { [System.Windows.Forms.Clipboard]::GetText() } else { '' }
            if ((Test-OtherClipboard $script:sync $idleRaw) -or [System.Windows.Forms.Clipboard]::ContainsImage() -or [System.Windows.Forms.Clipboard]::ContainsFileDropList()) {
                $script:sync.held=$false; $script:sync.backup=$null
                Stop-DraftSync '检测到新的复制操作，同步已暂停，新的剪贴板内容已保留。'
            }
        }
        if ((Get-SyncDecision $script:sync $draft.Text ([DateTime]::UtcNow)) -eq 'send') { Start-DraftSync }
    } catch {
        if ($null -ne $script:sync.pending -and ([DateTime]::UtcNow-$script:sync.pending.started).TotalSeconds -ge 18) {
            $script:sync.pending=$null; $resume.IsEnabled=$true
            Stop-DraftSync '剪贴板持续不可用，同步已暂停；请人工确认远端草稿。'
        } else { $status.Text='剪贴板正忙，正在等待；超时后会暂停同步。' }
    }
})
$draft.Add_TextChanged({
    $script:sync.changed=[DateTime]::UtcNow
    if ($script:sync.enabled -and -not $script:sync.paused -and $null -eq $script:sync.pending) { $status.Text='输入中… 暂停输入后同步。' }
})
# Never transmit an unfinished Chinese/Japanese IME composition.
[Windows.Input.TextCompositionManager]::AddPreviewTextInputStartHandler($draft,[Windows.Input.TextCompositionEventHandler]{
    $script:sync.composing=$true
})
[Windows.Input.TextCompositionManager]::AddPreviewTextInputHandler($draft,[Windows.Input.TextCompositionEventHandler]{
    $script:sync.composing=$false; $script:sync.changed=[DateTime]::UtcNow
})
$draft.Add_LostKeyboardFocus({
    $script:sync.composing=$false; $script:sync.changed=[DateTime]::UtcNow
})
$live.Add_Unchecked({
    $script:sync.enabled=$false
    if ($script:sync.held -and $null -eq $script:sync.pending) { Restore-BridgeClipboard $script:sync.expected }
    $status.Text='同步已暂停。本地内容和远端草稿保留；已发出的更新仍可能完成。'
})
$live.Add_Checked({
    $script:sync.enabled=$true; $script:sync.changed=[DateTime]::UtcNow
    if ($script:sync.paused) { $status.Text='请处理远端草稿后点击「重新连接空草稿」。' }
    else { $status.Text='实时同步已开启。' }
})
$resume.Add_Click({
    if ($null -ne $script:sync.pending) { return }
    $script:sync.stream=[Guid]::NewGuid().ToString('N'); $script:sync.seq=0
    $script:sync.confirmed=''; $script:sync.paused=$false
    $script:sync.changed=[DateTime]::UtcNow
    $live.IsChecked=$true; $script:sync.enabled=$true
    $status.Text='等待同步到空草稿；远端有内容时会拒绝覆盖。'
})
$window.Add_Closing({
    if ($script:sync.held) { Restore-BridgeClipboard $script:sync.expected }
    $timer.Stop()
    [Array]::Clear($key,0,$key.Length)
    if ($panelAcquired) { $panelMutex.ReleaseMutex(); $panelMutex.Dispose() }
})
$timer.Start()
[void]$window.ShowDialog()
