param([string]$KeyPath, [switch]$FunctionsOnly)
$ErrorActionPreference = 'Stop'
function New-BridgePacket([byte[]]$Key, [string]$Id, [string]$Text) {
    $Text=$Text.Replace("`r`n","`n").Replace("`r","`n")
    if ([string]::IsNullOrWhiteSpace($Text) -or ([regex]::Matches($Text,'[\uD800-\uDBFF][\uDC00-\uDFFF]|[\s\S]')).Count -gt 4000 -or $Text -match '[\x00-\x08\x0B\x0C\x0E-\x1F]') { throw '请输入有效文字（最多 4000 个字符）。' }
    $payload = @{kind='draft';id=$Id;at=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds();text=$Text} | ConvertTo-Json -Compress
    $body = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($payload))
    $hmac = New-Object Security.Cryptography.HMACSHA256
    try { $hmac.Key=$Key; $mac=([BitConverter]::ToString($hmac.ComputeHash([Text.Encoding]::ASCII.GetBytes($body)))).Replace('-','').ToLowerInvariant() } finally { $hmac.Dispose() }
    $envelope = @{body=$body;mac=$mac} | ConvertTo-Json -Compress
    return 'HC-DRAFT/1 ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($envelope))
}
function Read-BridgeResult([byte[]]$Key, [string]$Id, [string]$Raw) {
    if (-not $Raw.StartsWith('HC-DRAFT/1 ') -or $Raw.Length -gt 65536) { return $null }
    try {
        $outer = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Raw.Substring(11))) | ConvertFrom-Json
        $hmac = New-Object Security.Cryptography.HMACSHA256
        try { $hmac.Key=$Key; $mac=([BitConverter]::ToString($hmac.ComputeHash([Text.Encoding]::ASCII.GetBytes($outer.body)))).Replace('-','').ToLowerInvariant() } finally { $hmac.Dispose() }
        if ($mac -cne $outer.mac) { return $null }
        $p = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($outer.body)) | ConvertFrom-Json
        $age = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [long]$p.at
        if ($p.kind -ne 'result' -or $p.id -cne $Id -or $age -lt -2 -or $age -gt 20 -or $p.status -notin @('filled','not_ready','has_draft','uncertain')) { return $null }
        return $p.status
    } catch { return $null }
}
if ($FunctionsOnly) { return }
Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Security
if (-not $KeyPath -or -not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) { throw '尚未配对本地输入框，请先完成输入桥接配置。' }
$key = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes($KeyPath),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
if ($key.Length -ne 32) { throw '输入框配对无效。' }
[xml]$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Title="Claude 本地输入框" Width="740" Height="540" MinWidth="580" MinHeight="440" Background="#F7FAFD" FontFamily="Microsoft YaHei UI" WindowStartupLocation="CenterScreen">
<Grid Margin="30">
<Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
<TextBlock Text="本地输入，远程继续" FontSize="28" FontWeight="SemiBold" Foreground="#173544"/>
<TextBlock Grid.Row="1" Text="填入远程 Claude 的消息框，检查后再到 Claude 中发送。" TextWrapping="Wrap" Foreground="#586F7F" Margin="0,12,0,20" FontSize="14"/>
<TextBox Name="Draft" Grid.Row="2" AcceptsReturn="True" AcceptsTab="True" TextWrapping="Wrap" VerticalScrollBarVisibility="Auto" Padding="16" FontSize="17" Background="White" BorderBrush="#BDCCDA" BorderThickness="1" AutomationProperties.Name="要填入远程 Claude 的文字"/>
<DockPanel Grid.Row="3" Margin="0,18,0,0" LastChildFill="False">
<Button Name="Fill" DockPanel.Dock="Right" Content="填入远程 Claude" Background="#2468A0" Foreground="White" Padding="20,11" FontSize="15" BorderThickness="0"/>
<TextBlock Text="保持远程桌面连接，Claude 消息框留空。" TextWrapping="Wrap" VerticalAlignment="Center" Foreground="#586F7F" Margin="0,0,14,0" Width="340"/>
</DockPanel>
<TextBlock Name="Status" Grid.Row="4" Text="支持中文与多行文字。传递期间会临时使用剪贴板。" TextWrapping="Wrap" Foreground="#586F7F" Margin="0,18,0,0" FontSize="13"/>
</Grid></Window>
'@
$reader = New-Object Xml.XmlNodeReader $xaml
$window = [Windows.Markup.XamlReader]::Load($reader)
$draft = $window.FindName('Draft'); $fill = $window.FindName('Fill'); $status = $window.FindName('Status')
$script:pending = $null
function Restore-BridgeClipboard([string]$Expected) {
    try {
        if ([System.Windows.Forms.Clipboard]::ContainsText() -and [System.Windows.Forms.Clipboard]::GetText() -ceq $Expected) {
            if ($null -ne $script:pending.backup) { [System.Windows.Forms.Clipboard]::SetDataObject($script:pending.backup,$true,10,100) }
            else { [System.Windows.Forms.Clipboard]::Clear() }
        }
    } catch {} # Never replace newer clipboard contents.
}
$timer = New-Object Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(500)
$timer.Add_Tick({
    if ($null -eq $script:pending) { return }
    try {
        $raw = if ([System.Windows.Forms.Clipboard]::ContainsText()) { [System.Windows.Forms.Clipboard]::GetText() } else { '' }
        $answer = Read-BridgeResult $key $script:pending.id $raw
        if ($answer) {
            $messages = @{filled='已填入 Claude 草稿。请到远程 Claude 检查并发送。';not_ready='未识别到唯一的 Claude 消息框。请打开对话页后重试。';has_draft='Claude 中已有草稿，请先处理原草稿。';uncertain='未确认填入结果。请检查远程 Claude，不要直接重复点击。'}
            $status.Text = $messages[$answer]
            Restore-BridgeClipboard $raw
            $script:pending=$null; $fill.IsEnabled=$true
        } elseif (([DateTime]::UtcNow - $script:pending.started).TotalSeconds -ge 18) {
            Restore-BridgeClipboard $script:pending.raw
            $script:pending=$null; $fill.IsEnabled=$true
            $status.Text='未收到确认。请检查远程桌面连接与 Claude 草稿，文字仍保留在这里。'
        }
    } catch { $status.Text='剪贴板正忙，正在等待确认。' }
})
$fill.Add_Click({
    try {
        $id=[Guid]::NewGuid().ToString('N')
        $raw=New-BridgePacket $key $id $draft.Text
        $backup=[System.Windows.Forms.Clipboard]::GetDataObject()
        $saved = New-Object System.Windows.Forms.DataObject
        if ($null -ne $backup) {
            foreach ($format in $backup.GetFormats($false)) {
                try { $saved.SetData($format,$backup.GetData($format,$false)) } catch {}
            }
        }
        $script:pending=@{id=$id;raw=$raw;started=[DateTime]::UtcNow;backup=$saved}
        $transfer = New-Object System.Windows.Forms.DataObject
        $transfer.SetText($raw)
        [System.Windows.Forms.Clipboard]::SetDataObject($transfer,$true,10,100)
        $fill.IsEnabled=$false
        $status.Text='正在填入远程 Claude… 请保持远程桌面连接。'
    } catch {
        $script:pending=$null; $fill.IsEnabled=$true
        $status.Text='未开始传递：'+$_.Exception.Message
    }
})
$window.Add_Closing({
    if ($null -ne $script:pending) { Restore-BridgeClipboard $script:pending.raw }
    $timer.Stop()
    [Array]::Clear($key,0,$key.Length)
})
$timer.Start()
[void]$window.ShowDialog()
