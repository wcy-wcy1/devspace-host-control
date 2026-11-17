param([string]$KeyPath, [switch]$FunctionsOnly)
$ErrorActionPreference = 'Stop'
function Get-DraftHash([string]$Text) {
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}
function Normalize-Draft([string]$Text) { return $Text.Replace("`r`n","`n").Replace("`r","`n") }
function New-BridgePacket([byte[]]$Key, [string]$Id, [string]$Text, [string]$Stream='', [int]$Sequence=0, [string]$Base='', [ValidateSet('sync','submit','poll','recover')][string]$Kind='sync', [string]$Turn='') {
    $Text=Normalize-Draft $Text
    $sync=$Stream -ne ''
    if ((-not $sync -and [string]::IsNullOrWhiteSpace($Text)) -or ([regex]::Matches($Text,'[\uD800-\uDBFF][\uDC00-\uDFFF]|[\s\S]')).Count -gt 4000 -or $Text -match '[\x00-\x08\x0B\x0C\x0E-\x1F]') { throw '请输入有效文字（最多 4000 个字符）。' }
    $p=@{kind='draft';id=$Id;at=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds();text=$Text}
    $prefix='HC-DRAFT/1 '
    if ($Kind -ne 'sync' -and -not $sync) { throw '聊天操作需要配对会话。' }
    if ($sync) {
        if ($Stream -cnotmatch '^[a-f0-9]{32}$' -or ($Kind -in @('sync','submit') -and ($Sequence -lt 1 -or $Base -cnotmatch '^[a-f0-9]{64}$'))) { throw '同步版本无效。' }
        $p.kind='sync'; $p.stream=$Stream; $p.seq=$Sequence; $p.base=$Base; $prefix='HC-DRAFT/2 '
        if ($Kind -eq 'submit') {
            if ([string]::IsNullOrWhiteSpace($Text)) { throw '不能发送空消息。' }
            $p.kind='submit'; $prefix='HC-DRAFT/3 '
        } elseif ($Kind -eq 'recover') {
            if ([string]::IsNullOrWhiteSpace($Text)) { throw '请保留上一条发送的文字，用于核对回复。' }
            $p=@{kind='recover';id=$Id;at=$p.at;stream=$Stream;text=$Text}; $prefix='HC-DRAFT/3 '
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
    $delay=if($null -ne $State.inputDelayMs){$State.inputDelayMs}else{600}
    if (($Now-$State.changed).TotalMilliseconds -lt $delay) { return 'wait' }
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
Add-Type -AssemblyName System.Net.Http
$panelMutex=New-Object Threading.Mutex($false,'Local\HostControl-Claude-Chat-Panel-v4')
$panelAcquired=$false
try { $panelAcquired=$panelMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $panelAcquired=$true }
if (-not $panelAcquired) { $panelMutex.Dispose(); throw '本地输入框已经打开，请使用已有窗口。' }
if (-not $KeyPath -or -not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) { throw '尚未配对本地输入框，请先完成输入桥接配置。' }
$key=[Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes($KeyPath),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
if ($key.Length -ne 32) { throw '输入框配对无效。' }
$script:relay=$null
$relayPath=Join-Path (Split-Path $KeyPath) 'relay-state.dpapi'
if(Test-Path -LiteralPath $relayPath){
    $protected=[IO.File]::ReadAllBytes($relayPath)
    $header=[Text.Encoding]::ASCII.GetBytes("HOSTCONTROL-DPAPI`n")
    if($protected.Length -le $header.Length -or [Text.Encoding]::ASCII.GetString($protected,0,$header.Length) -cne "HOSTCONTROL-DPAPI`n"){throw '低延迟连接配置无效。'}
    $plain=[Security.Cryptography.ProtectedData]::Unprotect($protected[$header.Length..($protected.Length-1)],$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
    try{$endpoint=[Text.Encoding]::UTF8.GetString($plain)|ConvertFrom-Json}finally{[Array]::Clear($plain,0,$plain.Length)}
    $uri=[Uri]$endpoint.url
    if($uri.Scheme -ne 'http' -or $uri.Host -ne '127.0.0.1' -or $uri.AbsolutePath -ne '/input-relay' -or $uri.UserInfo -ne '' -or $uri.Query -ne '' -or $uri.Fragment -ne '' -or $endpoint.token -cnotmatch '^[a-f0-9]{64}$'){throw '低延迟连接必须使用私有本机端点。'}
    $handler=New-Object System.Net.Http.HttpClientHandler; $handler.UseProxy=$false
    $http=[System.Net.Http.HttpClient]::new($handler);$http.Timeout=[TimeSpan]::FromSeconds(2)
    $http.DefaultRequestHeaders.Authorization=[System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer',$endpoint.token)
    $script:relay=@{http=$http;url=$endpoint.url;session=[Guid]::NewGuid().ToString('N');opened=$false}
    $endpoint=$null
}
function New-RelayTask($Body){
    $Body.session=$script:relay.session
    $content=[System.Net.Http.StringContent]::new(($Body|ConvertTo-Json -Compress),[Text.Encoding]::UTF8,'application/json')
    try{return $script:relay.http.PostAsync($script:relay.url,$content)}catch{$content.Dispose();throw}
}
function Invoke-RelayLocal($Body){
    $response=(New-RelayTask $Body).GetAwaiter().GetResult()
    try{if(-not $response.IsSuccessStatusCode){throw '本机代理未就绪、输入会话过期或已有其它面板连接；请重新连接。'}}finally{if($response.RequestMessage){$response.RequestMessage.Dispose()};$response.Dispose()}
}
[xml]$xaml=@'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" Title="Claude 本地对话" Width="840" Height="840" MinWidth="660" MinHeight="680" Background="#F5F6F8" FontFamily="Segoe UI Variable, Segoe UI, Microsoft YaHei UI" WindowStartupLocation="CenterScreen" UseLayoutRounding="True" TextOptions.TextFormattingMode="Display">
<Window.Resources>
  <SolidColorBrush x:Key="Ink" Color="#202126"/>
  <SolidColorBrush x:Key="Secondary" Color="#666B76"/>
  <SolidColorBrush x:Key="Accent" Color="#007AFF"/>
  <SolidColorBrush x:Key="Surface" Color="#FFFFFF"/>
  <Style TargetType="Button">
    <Setter Property="Foreground" Value="{StaticResource Ink}"/><Setter Property="Background" Value="#F1F2F5"/>
    <Setter Property="Padding" Value="13,8"/><Setter Property="FontSize" Value="12"/><Setter Property="Cursor" Value="Hand"/>
    <Setter Property="BorderBrush" Value="Transparent"/><Setter Property="BorderThickness" Value="1.5"/><Setter Property="FocusVisualStyle" Value="{x:Null}"/>
    <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="Button">
      <Border x:Name="Chrome" Background="{TemplateBinding Background}" CornerRadius="11" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}">
        <ContentPresenter Margin="{TemplateBinding Padding}" HorizontalAlignment="Center" VerticalAlignment="Center"/>
      </Border>
      <ControlTemplate.Triggers>
        <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="Chrome" Property="Opacity" Value="0.8"/></Trigger>
        <Trigger Property="IsPressed" Value="True"><Setter TargetName="Chrome" Property="Opacity" Value="0.6"/></Trigger>
        <Trigger Property="IsKeyboardFocused" Value="True"><Setter Property="BorderBrush" Value="{StaticResource Accent}"/></Trigger>
        <Trigger Property="IsEnabled" Value="False"><Setter Property="Opacity" Value="0.4"/></Trigger>
      </ControlTemplate.Triggers>
    </ControlTemplate></Setter.Value></Setter>
  </Style>
  <Style x:Key="Card" TargetType="Border">
    <Setter Property="Background" Value="{StaticResource Surface}"/><Setter Property="CornerRadius" Value="20"/>
    <Setter Property="Padding" Value="24"/><Setter Property="BorderBrush" Value="#E4E6EC"/><Setter Property="BorderThickness" Value="0.75"/>
  </Style>
  <Style x:Key="Composer" TargetType="Border" BasedOn="{StaticResource Card}">
    <Setter Property="Effect"><Setter.Value><DropShadowEffect Color="#253552" BlurRadius="22" ShadowDepth="3" Opacity="0.045"/></Setter.Value></Setter>
    <Style.Triggers><Trigger Property="IsKeyboardFocusWithin" Value="True"><Setter Property="BorderBrush" Value="#93BFF2"/></Trigger></Style.Triggers>
  </Style>
  <Style TargetType="TextBox">
    <Setter Property="Foreground" Value="{StaticResource Ink}"/><Setter Property="Background" Value="Transparent"/><Setter Property="BorderThickness" Value="0"/>
    <Setter Property="FontSize" Value="15"/><Setter Property="Padding" Value="0,4"/><Setter Property="SelectionBrush" Value="#99C7FF"/>
    <Setter Property="FocusVisualStyle" Value="{x:Null}"/>
  </Style>
  <Style x:Key="Switch" TargetType="CheckBox">
    <Setter Property="Cursor" Value="Hand"/><Setter Property="VerticalContentAlignment" Value="Center"/><Setter Property="FocusVisualStyle" Value="{x:Null}"/>
    <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="CheckBox">
      <StackPanel Orientation="Horizontal"><Border x:Name="Track" Width="38" Height="23" CornerRadius="12" Background="#D1D1D6" BorderBrush="Transparent" BorderThickness="1.5"><Ellipse x:Name="Thumb" Fill="White" Width="17" Height="17" Margin="1,0" HorizontalAlignment="Left"/></Border><ContentPresenter Margin="9,0,0,0" VerticalAlignment="Center"/></StackPanel>
      <ControlTemplate.Triggers>
        <Trigger Property="IsChecked" Value="True"><Setter TargetName="Track" Property="Background" Value="#30B85B"/><Setter TargetName="Thumb" Property="HorizontalAlignment" Value="Right"/></Trigger>
        <Trigger Property="IsKeyboardFocused" Value="True"><Setter TargetName="Track" Property="BorderBrush" Value="{StaticResource Accent}"/></Trigger>
        <Trigger Property="IsEnabled" Value="False"><Setter Property="Opacity" Value="0.4"/></Trigger>
      </ControlTemplate.Triggers>
    </ControlTemplate></Setter.Value></Setter>
  </Style>
</Window.Resources>
<Grid Margin="30,26,30,20">
  <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
  <Grid Margin="3,0,3,22">
    <Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
    <StackPanel><TextBlock Text="Claude" FontSize="27" FontWeight="SemiBold" Foreground="{StaticResource Ink}"/><TextBlock Text="本地输入，远程协作" FontSize="13" Foreground="{StaticResource Secondary}" Margin="0,5,0,0"/></StackPanel>
    <Border Grid.Column="1" Background="#EAF0F8" CornerRadius="12" Padding="11,6" VerticalAlignment="Center"><TextBlock Name="ConnectionState" Text="等待输入" Foreground="#36648F" FontSize="11"/></Border>
  </Grid>
  <Border Grid.Row="1" Style="{StaticResource Card}">
    <Grid><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/></Grid.RowDefinitions>
      <Grid Margin="0,0,0,20"><Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
        <StackPanel VerticalAlignment="Center"><TextBlock Text="Claude 的回复" FontSize="14" FontWeight="SemiBold" Foreground="{StaticResource Ink}"/><TextBlock Name="ReplyState" Text="发送消息后，回复会显示在这里" FontSize="12" Foreground="{StaticResource Secondary}" Margin="0,5,8,0" TextWrapping="Wrap"/></StackPanel>
        <StackPanel Grid.Column="1" Orientation="Horizontal" VerticalAlignment="Top"><Button Name="RecoverReply" Content="取回回复" ToolTip="仅读取远程最后一条匹配消息的回复，不重新发送。" Margin="0,0,7,0"/><Button Name="CopyReply" Content="复制"/></StackPanel>
      </Grid>
      <TextBox Name="Reply" Grid.Row="1" IsReadOnly="True" TextWrapping="Wrap" VerticalScrollBarVisibility="Auto" AutomationProperties.Name="Claude 最新回复"/>
    </Grid>
  </Border>
  <Border Grid.Row="2" Style="{StaticResource Composer}" Margin="0,18,0,0">
    <Grid><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
      <TextBlock Text="你的消息" FontSize="13" FontWeight="SemiBold" Foreground="{StaticResource Ink}"/>
      <Grid Grid.Row="1" Margin="0,10,0,16">
        <TextBox Name="Draft" Height="112" MinHeight="90" AcceptsReturn="True" AcceptsTab="True" TextWrapping="Wrap" VerticalScrollBarVisibility="Auto" AutomationProperties.Name="实时同步到远程 Claude 的草稿"/>
        <TextBlock Text="写下你的想法…" FontSize="15" Foreground="#797E88" Margin="0,4,0,0" IsHitTestVisible="False"><TextBlock.Style><Style TargetType="TextBlock"><Setter Property="Visibility" Value="Collapsed"/><Style.Triggers><DataTrigger Binding="{Binding Text, ElementName=Draft}" Value=""><Setter Property="Visibility" Value="Visible"/></DataTrigger></Style.Triggers></Style></TextBlock.Style></TextBlock>
      </Grid>
      <Grid Grid.Row="2"><Grid.ColumnDefinitions><ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
        <CheckBox Name="Live" Style="{StaticResource Switch}" IsChecked="True" Content="同步输入" FontSize="12" Foreground="{StaticResource Secondary}" VerticalAlignment="Center"/>
        <Button Name="Resume" Grid.Column="2" Content="连接空对话" Background="Transparent" Foreground="{StaticResource Accent}" Margin="8,0,8,0" ToolTip="在远程新建空对话后，点这里连接。已有草稿不会被覆盖。"/>
        <Button Name="Send" Grid.Column="3" Content="发送 ↵" Background="{StaticResource Accent}" Foreground="White" FontSize="13" FontWeight="SemiBold" Padding="20,9"/>
      </Grid>
    </Grid>
  </Border>
  <StackPanel Grid.Row="3" Margin="5,15,5,0"><TextBlock Text="Enter 发送，Shift+Enter 换行" FontSize="11" Foreground="{StaticResource Secondary}"/><TextBlock Name="Status" Text="请保持远程桌面连接，并先打开一个空对话。" FontSize="11" Foreground="{StaticResource Secondary}" TextWrapping="Wrap" Margin="0,5,0,0"/></StackPanel>
</Grid>
</Window>
'@
$reader=New-Object Xml.XmlNodeReader $xaml
$window=[Windows.Markup.XamlReader]::Load($reader)
$draft=$window.FindName('Draft'); $live=$window.FindName('Live'); $resume=$window.FindName('Resume'); $status=$window.FindName('Status')
$send=$window.FindName('Send'); $reply=$window.FindName('Reply'); $replyState=$window.FindName('ReplyState'); $copyReply=$window.FindName('CopyReply')
$recoverReply=$window.FindName('RecoverReply'); $connectionState=$window.FindName('ConnectionState')
$script:sync=@{stream=[Guid]::NewGuid().ToString('N');seq=0;confirmed='';pending=$null;clipHashes=@();held=$false;backup=$null;expected='';enabled=$true;paused=$false;composing=$false;sendWanted=$false;stopRequested=$false;awaiting=$false;replyActive=$false;replyStream='';turn='';pollAt=[DateTime]::UtcNow;changed=[DateTime]::UtcNow}
$script:sync.inputDelayMs=if($script:relay){0}else{600}
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
    $connectionState.Text='已暂停'
    if($script:sync.replyActive -or $script:sync.awaiting){$replyState.Text='回复更新已暂停 · 可点击「取回回复」'}
    $script:sync.paused=$true
    $script:sync.recoverOnly=$false
    $script:sync.sendWanted=$false; $script:sync.awaiting=$false; $script:sync.replyActive=$false
    $draft.IsReadOnly=$false; $send.IsEnabled=$true
    $status.Text=$Message+' 本地文字已保留；处理远端草稿后点击「连接空对话」。'
}
function Start-DraftSync([string]$Kind='sync') {
    try {
        $text=if($Kind -eq 'poll'){''}elseif($Kind -eq 'recover'){$script:sync.recoveryText}else{Normalize-Draft $draft.Text}
        $id=[Guid]::NewGuid().ToString('N')
        $sequence=$script:sync.seq+1
        $stream=if($Kind -eq 'poll'){$script:sync.replyStream}else{$script:sync.stream}
        $raw=New-BridgePacket $key $id $text $stream $sequence (Get-DraftHash $script:sync.confirmed) $Kind $script:sync.turn
        if($script:relay){
            if(-not $script:relay.opened){Invoke-RelayLocal @{action='open'};$script:relay.opened=$true}
            $script:sync.pending=@{id=$id;raw=$raw;text=$text;kind=$Kind;stream=$stream;started=[DateTime]::UtcNow;readTask=$null}
            Invoke-RelayLocal @{action='send';ticket=$id;packet=$raw}
        }else{
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
        }
        if($Kind -ne 'poll'){$script:sync.seq=$sequence}
        $resume.IsEnabled=$false
        $recoverReply.IsEnabled=$false
        if($Kind -eq 'recover'){$replyState.Text='正在核对并取回回复…';$status.Text='只读取对应回复，不会重新发送消息。'}
        elseif($Kind -eq 'submit'){$status.Text='正在发送当前已同步的消息… 请勿重复发送。'}
        elseif($Kind -eq 'poll'){$replyState.Text='正在获取 Claude 回复…'}
        else{$status.Text='正在同步… 最新内容确认后再发送。'}
    } catch {
        $script:sync.pending=$null; $resume.IsEnabled=$true; $recoverReply.IsEnabled=$true
        Stop-DraftSync ('未开始同步：'+$_.Exception.Message)
    }
}
$timer=New-Object Windows.Threading.DispatcherTimer
$timer.Interval=[TimeSpan]::FromMilliseconds(100)
$timer.Add_Tick({
    try {
        $pending=$script:sync.pending
        if ($null -ne $pending) {
            if($script:relay){
                $raw=$pending.raw
                if($null -eq $pending.readTask){$pending.readTask=New-RelayTask @{action='read';ticket=$pending.id}}
                elseif($pending.readTask.IsCompleted){
                    $response=$pending.readTask.GetAwaiter().GetResult();$pending.readTask=$null
                    try{
                        if(-not $response.IsSuccessStatusCode){throw '低延迟通道断开；不会自动重发，请检查远端结果。'}
                        if([int]$response.StatusCode -ne 204){
                            $delivered=$response.Content.ReadAsStringAsync().GetAwaiter().GetResult()|ConvertFrom-Json
                            if($delivered.ticket -cne $pending.id -or $delivered.packet -isnot [string]){throw '低延迟响应不匹配。'}
                            $raw=$delivered.packet
                        }
                    }finally{if($response.RequestMessage){$response.RequestMessage.Dispose()};$response.Dispose()}
                }
            }else{
                $raw=if ([System.Windows.Forms.Clipboard]::ContainsText()) { [System.Windows.Forms.Clipboard]::GetText() } else { '' }
            }
            $chat=$null
            if($pending.kind -eq 'sync'){$answer=Read-BridgeResult $key $pending.id $raw -Sync}
            else{$chat=Read-ChatResult $key $pending.id $raw; $answer=if($null -ne $chat){$chat.status}else{$null}}
            if ($answer) {
                $connectionState.Text=if($script:relay){'低延迟连接'}else{'兼容连接'}
                $script:sync.expected=$raw
                # Keep ownership through consecutive updates. Restoring between
                # packets races RDP's asynchronous clipboard announcements.
                if ($answer -in @('conflict','not_ready','changed') -or ($script:sync.paused -and -not $script:sync.recoverOnly) -or
                    (-not $script:sync.enabled -and -not $script:sync.sendWanted -and -not $script:sync.replyActive)) { Restore-BridgeClipboard $raw }
                $script:sync.pending=$null; $resume.IsEnabled=$true; $recoverReply.IsEnabled=$true
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
                } elseif($pending.kind -in @('poll','recover') -and $answer -in @('waiting','streaming','complete')) {
                    if($pending.kind -eq 'recover'){
                        $script:sync.replyStream=$pending.stream; $script:sync.turn=$pending.id
                        $script:sync.watchStarted=[DateTime]::UtcNow; $script:sync.replyActive=$true
                    }
                    $script:sync.pollAt=[DateTime]::UtcNow.AddMilliseconds($(if($script:relay){250}else{1000}))
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
                        $status.Text=if($script:sync.recoverOnly){'回复已取回，没有重新发送。本地文字保留；发送新消息前先连接空草稿。'}else{'回复已显示在上方。Enter 发送下一条，Shift+Enter 换行。'}
                        $script:sync.recoverOnly=$false
                        if(-not $script:sync.enabled){Restore-BridgeClipboard $raw}
                    }
                } elseif ($answer -eq 'synced') {
                    $script:sync.confirmed=$pending.text
                    if ($script:sync.paused -or -not $script:sync.enabled) { $status.Text='同步已暂停，已发出的更新已确认。' }
                    elseif ((Normalize-Draft $draft.Text) -ceq $pending.text) { $status.Text='已同步到 Claude 草稿。Enter 发送，Shift+Enter 换行。' }
                    else { $status.Text='正在等待同步最新输入…' }
                } else {
                    $messages=@{not_ready='未找到可用的 Claude 对话。';conflict='远端草稿或对话发生变化，或最后一条消息与待取回文字不符。';changed='远端会话已改变，停止获取回复。';uncertain='无法确认远端操作结果，请检查 Claude，切勿直接重发。'}
                    Stop-DraftSync $messages[$answer]
                }
            } elseif (([DateTime]::UtcNow-$pending.started).TotalSeconds -ge 18) {
                Restore-BridgeClipboard $pending.raw
                $script:sync.pending=$null; $resume.IsEnabled=$true; $recoverReply.IsEnabled=$true
                Stop-DraftSync '未收到确认；请检查远程连接与 Claude 草稿，同步不会自动重试。'
            } elseif (-not $script:relay -and $raw -cne $pending.raw -and (Test-OtherClipboard $script:sync $raw)) {
                $script:sync.held=$false; $script:sync.backup=$null
                $script:sync.pending=$null; $resume.IsEnabled=$true; $recoverReply.IsEnabled=$true
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
        elseif($null -eq $script:sync.pending -and $script:sync.replyActive -and (-not $script:sync.paused -or $script:sync.recoverOnly) -and [DateTime]::UtcNow -ge $script:sync.pollAt){
            $elapsed=([DateTime]::UtcNow-$script:sync.watchStarted).TotalSeconds
            if(($script:sync.awaiting -and $elapsed -gt 40) -or $elapsed -gt 600){Stop-DraftSync '等待回复超时，请在远端确认结果，不会自动重发。'}
            else{Start-DraftSync 'poll'}
        }
    } catch {
        if($script:relay -and $null -ne $script:sync.pending){
            $script:sync.pending=$null;$resume.IsEnabled=$true;$recoverReply.IsEnabled=$true
            Stop-DraftSync '低延迟连接异常；请核实远端结果，不会自动重试。';return
        }
        if ($null -ne $script:sync.pending -and ([DateTime]::UtcNow-$script:sync.pending.started).TotalSeconds -ge 18) {
            $script:sync.pending=$null; $resume.IsEnabled=$true; $recoverReply.IsEnabled=$true
            Stop-DraftSync '剪贴板持续不可用，同步已暂停；请人工确认远端草稿。'
        } else { $status.Text='剪贴板正忙，正在等待；超时后会暂停同步。' }
    }
})
function Request-ChatSend {
    if($script:sync.composing -or $script:sync.sendWanted -or $script:sync.awaiting){return}
    if($script:sync.paused){$status.Text='请先处理远端草稿并点击「连接空对话」。';return}
    if($script:sync.replyActive){$status.Text='Claude 仍在回复，可以先输入，完成后再发送。';return}
    if([string]::IsNullOrWhiteSpace($draft.Text)){$status.Text='请输入消息后再发送。';return}
    $script:sync.sendWanted=$true; $draft.IsReadOnly=$true; $send.IsEnabled=$false
    $script:sync.stopRequested=$false
    $status.Text='正在确认最新输入，随后发送一次…'
}
$send.Add_Click({Request-ChatSend})
$recoverReply.Add_Click({
    if($null -ne $script:sync.pending -or $script:sync.composing){return}
    $text=if($script:sync.sentText){$script:sync.sentText}else{Normalize-Draft $draft.Text}
    if([string]::IsNullOrWhiteSpace($text)){$status.Text='请在下方填入要核对的上一条消息，再点「取回回复」。不会发送这段文字。';return}
    $live.IsChecked=$false
    Stop-DraftSync '已暂停草稿同步，准备仅读取回复。'
    $script:sync.recoveryText=$text; $script:sync.recoverOnly=$true
    $script:sync.stopRequested=$false; $script:sync.replyActive=$true
    $script:sync.watchStarted=[DateTime]::UtcNow
    Start-DraftSync 'recover'
})
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
    if ($script:sync.enabled -and -not $script:sync.paused -and $null -eq $script:sync.pending) { $status.Text=if($script:relay){'输入正在同步…'}else{'输入中… 暂停输入后同步。'} }
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
    if($script:sync.replyActive -or $script:sync.awaiting){$replyState.Text='回复更新已暂停 · 可点击「取回回复」'}
    $script:sync.recoverOnly=$false
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
    if ($script:sync.paused) { $status.Text='请处理远端草稿后点击「连接空对话」。' }
    else { $status.Text='实时同步已开启。' }
})
$resume.Add_Click({
    if ($null -ne $script:sync.pending) { return }
    if($script:relay -and $script:relay.opened){try{Invoke-RelayLocal @{action='close'}}catch{};$script:relay.opened=$false}
    $script:sync.stream=[Guid]::NewGuid().ToString('N'); $script:sync.seq=0
    $script:sync.confirmed=''; $script:sync.paused=$false
    $script:sync.replyActive=$false; $script:sync.awaiting=$false; $script:sync.sendWanted=$false
    $script:sync.stopRequested=$false
    $script:sync.recoverOnly=$false
    $draft.IsReadOnly=$false; $send.IsEnabled=$true
    $script:sync.changed=[DateTime]::UtcNow
    $live.IsChecked=$true; $script:sync.enabled=$true
    $status.Text='等待同步到空草稿；远端有内容时会拒绝覆盖。'
})
$window.Add_Closing({
    if ($script:sync.held) { Restore-BridgeClipboard $script:sync.expected }
    $timer.Stop()
    if($script:relay){if($script:relay.opened){try{Invoke-RelayLocal @{action='close'}}catch{}};$script:relay.http.Dispose()}
    [Array]::Clear($key,0,$key.Length)
    if ($panelAcquired) { $panelMutex.ReleaseMutex(); $panelMutex.Dispose() }
})
$timer.Start()
[void]$window.ShowDialog()
