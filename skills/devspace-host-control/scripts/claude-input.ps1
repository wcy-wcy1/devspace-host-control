param([string]$KeyPath, [switch]$FunctionsOnly)
$ErrorActionPreference = 'Stop'
function Get-DraftHash([string]$Text) {
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}
function Normalize-Draft([string]$Text) { return $Text.Replace("`r`n","`n").Replace("`r","`n") }
function New-BridgePacket([byte[]]$Key, [string]$Id, [string]$Text, [string]$Stream='', [int]$Sequence=0, [string]$Base='', [ValidateSet('sync','submit','poll')][string]$Kind='sync', [string]$Turn='') {
    $Text=Normalize-Draft $Text
    $sync=$Stream -ne ''
    if ((-not $sync -and [string]::IsNullOrWhiteSpace($Text)) -or ([regex]::Matches($Text,'[\uD800-\uDBFF][\uDC00-\uDFFF]|[\s\S]')).Count -gt 4000 -or $Text -match '[\x00-\x08\x0B\x0C\x0E-\x1F]') { throw '请输入有效文字（最多 4000 个字符）。' }
    $p=@{kind='draft';id=$Id;at=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds();text=$Text}
    $prefix='HC-DRAFT/1 '
    if ($Kind -ne 'sync' -and -not $sync) { throw '聊天操作需要配对会话。' }
    if ($sync) {
        if ($Stream -cnotmatch '^[a-f0-9]{32}$' -or ($Kind -ne 'poll' -and ($Sequence -lt 1 -or $Base -cnotmatch '^[a-f0-9]{64}$'))) { throw '同步版本无效。' }
        $p.kind='sync'; $p.stream=$Stream; $p.seq=$Sequence; $p.base=$Base; $prefix='HC-DRAFT/2 '
        if ($Kind -eq 'submit') {
            if ([string]::IsNullOrWhiteSpace($Text)) { throw '不能发送空消息。' }
            $p.kind='submit'; $prefix='HC-DRAFT/3 '
        } elseif ($Kind -eq 'poll') {
            if ($Turn -cnotmatch '^[a-f0-9]{32}$') { throw '回复标识无效。' }
            $p=@{kind='poll';id=$Id;at=$p.at;stream=$Stream;turn=$Turn}; $prefix='HC-DRAFT/3 '
        }
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
    if ($State.paused -or $State.composing -or $null -ne $State.pending) { return 'wait' }
    if ($State.sendWanted) {
        if ((Normalize-Draft $Text) -ceq $State.confirmed) { return 'submit' }
        return 'send'
    }
    if (-not $State.enabled) { return 'wait' }
    if ((Normalize-Draft $Text) -ceq $State.confirmed) { return 'wait' }
    if (($Now-$State.changed).TotalMilliseconds -lt 600) { return 'wait' }
    return 'send'
}
function Test-OtherClipboard($State, [string]$Raw) {
    # RDP can briefly expose no text, or echo a preceding clipboard revision.
    # Recognize our own recent values locally without retransmitting them.
    if ($Raw -eq '' -or $Raw.StartsWith('HC-DRAFT/2 ') -or $Raw.StartsWith('HC-DRAFT/3 ')) { return $false }
    return (Get-DraftHash (Normalize-Draft $Raw)) -notin $State.clipHashes
}
function Read-ChatResult([byte[]]$Key, [string]$Id, [string]$Raw) {
    if (-not $Raw.StartsWith('HC-DRAFT/3 ') -or $Raw.Length -gt 262144) { return $null }
    try {
        $outer=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Raw.Substring(11))) | ConvertFrom-Json
        $hmac=New-Object Security.Cryptography.HMACSHA256
        try { $hmac.Key=$Key; $mac=([BitConverter]::ToString($hmac.ComputeHash([Text.Encoding]::ASCII.GetBytes($outer.body)))).Replace('-','').ToLowerInvariant() } finally { $hmac.Dispose() }
        if ($mac -cne $outer.mac) { return $null }
        $p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($outer.body)) | ConvertFrom-Json
        $age=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()-[long]$p.at
        if ($p.kind -ne 'chat_result' -or $p.id -cne $Id -or $age -lt -2 -or $age -gt 20 -or $p.status -notin @('submitted','waiting','streaming','complete','changed','conflict','not_ready','uncertain')) { return $null }
        if ($null -ne $p.text -and ($p.text -isnot [string] -or $p.text.Length -gt 48000)) { return $null }
        if ($null -ne $p.accepted -and $p.accepted -isnot [bool]) { return $null }
        if ($null -ne $p.truncated -and $p.truncated -isnot [bool]) { return $null }
        if ($p.status -in @('streaming','complete') -and ($p.accepted -ne $true -or $p.text -isnot [string])) { return $null }
        return $p
    } catch { return $null }
}
function Get-EnterAction([bool]$Shift, [bool]$Composing, [bool]$Repeat) {
    if ($Composing) { return 'ime' }
    if ($Shift) { return 'newline' }
    if ($Repeat) { return 'ignore' }
    return 'submit'
}
if ($FunctionsOnly) { return }
Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Security
$panelMutex=New-Object Threading.Mutex($false,'Local\HostControl-Claude-Chat-Panel-v3')
$panelAcquired=$false
try { $panelAcquired=$panelMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $panelAcquired=$true }
if (-not $panelAcquired) { $panelMutex.Dispose(); throw '本地输入框已经打开，请使用已有窗口。' }
if (-not $KeyPath -or -not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) { throw '尚未配对本地输入框，请先完成输入桥接配置。' }
$key=[Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes($KeyPath),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
if ($key.Length -ne 32) { throw '输入框配对无效。' }
[xml]$xaml=@'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Title="Claude 本地对话" Width="800" Height="800" MinWidth="640" MinHeight="650" Background="#F7FAFD" FontFamily="Microsoft YaHei UI" WindowStartupLocation="CenterScreen">
<Grid Margin="30">
<Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="2*"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
<TextBlock Text="本地对话，远程 Claude" FontSize="28" FontWeight="SemiBold" Foreground="#173544"/>
<TextBlock Grid.Row="1" Text="Enter 发送 · Shift+Enter 换行 · 回复在这里更新，跨境连接有数秒延迟。" TextWrapping="Wrap" Foreground="#586F7F" Margin="0,12,0,16" FontSize="14"/>
<DockPanel Grid.Row="2" Margin="0,0,0,8"><Button Name="CopyReply" Content="复制回复" DockPanel.Dock="Right" Padding="10,4"/><TextBlock Name="ReplyState" Text="Claude 回复 · 发送后自动回显" Foreground="#586F7F" VerticalAlignment="Center"/></DockPanel>
<TextBox Name="Reply" Grid.Row="3" IsReadOnly="True" TextWrapping="Wrap" VerticalScrollBarVisibility="Auto" Padding="16" FontSize="16" Background="#EDF3F8" BorderBrush="#D3DFE8" AutomationProperties.Name="Claude 最新回复"/>
<TextBlock Grid.Row="4" Text="你的消息" Foreground="#586F7F" Margin="0,14,0,8"/>
<TextBox Name="Draft" Grid.Row="5" AcceptsReturn="True" AcceptsTab="True" TextWrapping="Wrap" VerticalScrollBarVisibility="Auto" Padding="16" FontSize="17" Background="White" BorderBrush="#BDCCDA" BorderThickness="1" AutomationProperties.Name="实时同步到远程 Claude 的草稿"/>
<DockPanel Grid.Row="6" Margin="0,16,0,0" LastChildFill="False">
<Button Name="Send" DockPanel.Dock="Right" Content="发送 ↵" Background="#2468A0" Foreground="White" Padding="20,11" FontSize="15" BorderThickness="0"/>
<Button Name="Resume" DockPanel.Dock="Right" Content="重新连接空草稿" Background="Transparent" Foreground="#2468A0" Padding="10,11" Margin="0,0,10,0" FontSize="13" BorderThickness="0"/>
<CheckBox Name="Live" IsChecked="True" Content="实时同步" VerticalAlignment="Center" Foreground="#173544" FontSize="15" Margin="0,0,20,0"/>
</DockPanel>
<TextBlock Name="Status" Grid.Row="7" Text="请保持远程桌面连接，Claude 消息框先留空。同步与回显期间使用剪贴板，暂停后恢复。" TextWrapping="Wrap" Foreground="#586F7F" Margin="0,16,0,0" FontSize="13"/>
</Grid></Window>
'@
$reader=New-Object Xml.XmlNodeReader $xaml
$window=[Windows.Markup.XamlReader]::Load($reader)
$draft=$window.FindName('Draft'); $live=$window.FindName('Live'); $resume=$window.FindName('Resume'); $status=$window.FindName('Status')
$send=$window.FindName('Send'); $reply=$window.FindName('Reply'); $replyState=$window.FindName('ReplyState'); $copyReply=$window.FindName('CopyReply')
$script:sync=@{stream=[Guid]::NewGuid().ToString('N');seq=0;confirmed='';pending=$null;clipHashes=@();held=$false;backup=$null;expected='';enabled=$true;paused=$false;composing=$false;sendWanted=$false;stopRequested=$false;awaiting=$false;replyActive=$false;replyStream='';turn='';pollAt=[DateTime]::UtcNow;changed=[DateTime]::UtcNow}
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
    $script:sync.sendWanted=$false; $script:sync.awaiting=$false; $script:sync.replyActive=$false
    $draft.IsReadOnly=$false; $send.IsEnabled=$true
    $status.Text=$Message+' 本地文字已保留；处理远端草稿后点击「重新连接空草稿」。'
}
function Start-DraftSync([string]$Kind='sync') {
    try {
        $text=if($Kind -eq 'poll'){''}else{Normalize-Draft $draft.Text}
        $id=[Guid]::NewGuid().ToString('N')
        $sequence=$script:sync.seq+1
        $stream=if($Kind -eq 'poll'){$script:sync.replyStream}else{$script:sync.stream}
        $raw=New-BridgePacket $key $id $text $stream $sequence (Get-DraftHash $script:sync.confirmed) $Kind $script:sync.turn
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
        $script:sync.pending=@{id=$id;raw=$raw;text=$text;kind=$Kind;stream=$stream;started=[DateTime]::UtcNow}
        $script:sync.expected=$raw
        $transfer=New-Object System.Windows.Forms.DataObject
        $transfer.SetText($raw)
        [System.Windows.Forms.Clipboard]::SetDataObject($transfer,$true,2,50)
        if($Kind -ne 'poll'){$script:sync.seq=$sequence}
        $resume.IsEnabled=$false
        if($Kind -eq 'submit'){$status.Text='正在发送当前已同步的消息… 请勿重复发送。'}
        elseif($Kind -eq 'poll'){$replyState.Text='正在获取 Claude 回复…'}
        else{$status.Text='正在同步… 最新内容确认后再发送。'}
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
            $chat=$null
            if($pending.kind -eq 'sync'){$answer=Read-BridgeResult $key $pending.id $raw -Sync}
            else{$chat=Read-ChatResult $key $pending.id $raw; $answer=if($null -ne $chat){$chat.status}else{$null}}
            if ($answer) {
                $script:sync.expected=$raw
                # Keep ownership through consecutive updates. Restoring between
                # packets races RDP's asynchronous clipboard announcements.
                if ($answer -in @('conflict','not_ready','changed') -or $script:sync.paused -or
                    (-not $script:sync.enabled -and -not $script:sync.sendWanted -and -not $script:sync.replyActive)) { Restore-BridgeClipboard $raw }
                $script:sync.pending=$null; $resume.IsEnabled=$true
                if($script:sync.stopRequested -and $pending.kind -ne 'sync'){
                    Stop-DraftSync '已停止发送确认与回复更新；已触发的发送仍可能完成，请先在远端核实。'
                    return
                }
                if($pending.kind -eq 'submit' -and $answer -in @('submitted','uncertain')){
                    $script:sync.sendWanted=$false; $script:sync.awaiting=$true
                    $script:sync.sentText=$pending.text; $script:sync.replyActive=$true
                    $script:sync.replyStream=$pending.stream; $script:sync.turn=$pending.id
                    $script:sync.watchStarted=[DateTime]::UtcNow; $script:sync.pollAt=[DateTime]::UtcNow
                    $reply.Text=''; $replyState.Text='等待 Claude 确认与回复…'
                    $status.Text='发送已触发一次，正在确认；不会自动重复发送。'
                } elseif($pending.kind -eq 'poll' -and $answer -in @('waiting','streaming','complete')) {
                    $script:sync.pollAt=[DateTime]::UtcNow.AddSeconds(1)
                    if($chat.accepted -eq $true -and $script:sync.awaiting){
                        if((Normalize-Draft $draft.Text) -cne $script:sync.sentText){Stop-DraftSync '本地文字意外变化，请检查远端消息。';return}
                        $script:sync.awaiting=$false; $draft.Text=''; $draft.IsReadOnly=$false
                        $script:sync.confirmed=''; $script:sync.stream=[Guid]::NewGuid().ToString('N'); $script:sync.seq=0
                        $status.Text='Claude 已接收消息。可以继续输入下一条。'
                    }
                    if($null -ne $chat.text){$reply.Text=$chat.text}
                    $replyState.Text=if($answer -eq 'complete'){'Claude 回复 · 已完成'}elseif($answer -eq 'streaming'){'Claude 回复 · 正在更新…'}else{'等待 Claude 回复…'}
                    if($chat.truncated -eq $true){$replyState.Text+='（显示前 24000 个字符）'}
                    if($answer -eq 'complete'){
                        $script:sync.replyActive=$false; $send.IsEnabled=$true
                        $status.Text='回复已显示在上方。Enter 发送下一条，Shift+Enter 换行。'
                        if(-not $script:sync.enabled){Restore-BridgeClipboard $raw}
                    }
                } elseif ($answer -eq 'synced') {
                    $script:sync.confirmed=$pending.text
                    if ($script:sync.paused -or -not $script:sync.enabled) { $status.Text='同步已暂停，已发出的更新已确认。' }
                    elseif ((Normalize-Draft $draft.Text) -ceq $pending.text) { $status.Text='已同步到 Claude 草稿。Enter 发送，Shift+Enter 换行。' }
                    else { $status.Text='正在等待同步最新输入…' }
                } else {
                    $messages=@{not_ready='未找到可用的 Claude 对话。';conflict='远端草稿或对话发生变化，同步已暂停。';changed='远端会话已改变，停止获取回复。';uncertain='无法确认远端操作结果，请检查 Claude，切勿直接重发。'}
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
        $decision=Get-SyncDecision $script:sync $draft.Text ([DateTime]::UtcNow)
        if($decision -eq 'send'){Start-DraftSync}
        elseif($decision -eq 'submit'){Start-DraftSync 'submit'}
        elseif($null -eq $script:sync.pending -and $script:sync.replyActive -and -not $script:sync.paused -and [DateTime]::UtcNow -ge $script:sync.pollAt){
            $elapsed=([DateTime]::UtcNow-$script:sync.watchStarted).TotalSeconds
            if(($script:sync.awaiting -and $elapsed -gt 40) -or $elapsed -gt 600){Stop-DraftSync '等待回复超时，请在远端确认结果，不会自动重发。'}
            else{Start-DraftSync 'poll'}
        }
    } catch {
        if ($null -ne $script:sync.pending -and ([DateTime]::UtcNow-$script:sync.pending.started).TotalSeconds -ge 18) {
            $script:sync.pending=$null; $resume.IsEnabled=$true
            Stop-DraftSync '剪贴板持续不可用，同步已暂停；请人工确认远端草稿。'
        } else { $status.Text='剪贴板正忙，正在等待；超时后会暂停同步。' }
    }
})
function Request-ChatSend {
    if($script:sync.composing -or $script:sync.sendWanted -or $script:sync.awaiting){return}
    if($script:sync.paused){$status.Text='请先处理远端草稿并点击「重新连接空草稿」。';return}
    if($script:sync.replyActive){$status.Text='Claude 仍在回复，可以先输入，完成后再发送。';return}
    if([string]::IsNullOrWhiteSpace($draft.Text)){$status.Text='请输入消息后再发送。';return}
    $script:sync.sendWanted=$true; $draft.IsReadOnly=$true; $send.IsEnabled=$false
    $script:sync.stopRequested=$false
    $status.Text='正在确认最新输入，随后发送一次…'
}
$send.Add_Click({Request-ChatSend})
$draft.Add_PreviewKeyDown({
    param($sender,$event)
    if($event.Key -ne [Windows.Input.Key]::Return){return}
    $shift=([Windows.Input.Keyboard]::Modifiers -band [Windows.Input.ModifierKeys]::Shift) -ne 0
    $action=Get-EnterAction $shift $script:sync.composing $event.IsRepeat
    if($action -in @('ime','newline')){return}
    $event.Handled=$true
    if($action -eq 'submit'){Request-ChatSend}
})
$copyReply.Add_Click({
    if([string]::IsNullOrEmpty($reply.Text)){return}
    $live.IsChecked=$false
    Stop-DraftSync '已停止回复更新。'
    if($script:sync.held){Restore-BridgeClipboard $script:sync.expected}
    [System.Windows.Forms.Clipboard]::SetText($reply.Text)
    $status.Text='回复已复制。同步已暂停，重新连接空草稿后可继续。'
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
    $script:sync.stopRequested=$true
    if($script:sync.sendWanted -or $script:sync.awaiting -or $script:sync.replyActive -or ($null -ne $script:sync.pending -and $script:sync.pending.kind -eq 'submit')){
        $script:sync.paused=$true
    }
    $draft.IsReadOnly=$false; $send.IsEnabled=$true
    $script:sync.replyActive=$false; $script:sync.sendWanted=$false
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
    $script:sync.replyActive=$false; $script:sync.awaiting=$false; $script:sync.sendWanted=$false
    $script:sync.stopRequested=$false
    $draft.IsReadOnly=$false; $send.IsEnabled=$true
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
