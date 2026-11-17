import base64
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

import input_bridge as bridge


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows PowerShell transport')
class PanelReconnectTests(unittest.TestCase):
    def run_ps(self, code):
        script = Path(__file__).with_name('claude-input.ps1')
        code = ". '" + str(script).replace("'", "''") + "' -FunctionsOnly\n" + code
        encoded = base64.b64encode(code.encode('utf-16-le')).decode()
        result = subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand', encoded],
                                capture_output=True, text=True, timeout=20, check=True)
        return result.stdout.strip().splitlines()

    def test_reload_uses_new_endpoint_token_and_session_and_rejects_missing_or_external_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state = str(Path(directory) / 'relay-state.dpapi').replace("'", "''")
            code = r"""
Add-Type -AssemblyName System.Security
$path='STATE_PATH'
function Save-State($url,$token){
 $bytes=[Text.Encoding]::UTF8.GetBytes((@{url=$url;token=$token}|ConvertTo-Json -Compress))
 $data=[Security.Cryptography.ProtectedData]::Protect($bytes,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
 [IO.File]::WriteAllBytes($path,([Text.Encoding]::ASCII.GetBytes("HOSTCONTROL-DPAPI`n")+$data))
}
Save-State 'http://127.0.0.1:31001/input-relay' ('a'*64)
$first=New-RelayConnection $path -Required
try{
 Save-State 'http://127.0.0.1:31002/input-relay' ('b'*64)
 $second=New-RelayConnection $path -Required
 try{
  ($first.url -cne $second.url)
  ($first.session -cne $second.session)
  ($second.http.DefaultRequestHeaders.Authorization.Parameter -ceq ('b'*64))
  (-not $second.opened)
 }finally{$second.http.Dispose()}
 foreach($url in @('http://example.com/input-relay','https://127.0.0.1/input-relay','http://127.0.0.1/input-relay?token=x')){
  Save-State $url ('c'*64)
  try{$bad=New-RelayConnection $path -Required;$bad.http.Dispose();throw 'accepted unsafe endpoint'}
  catch{if($_.Exception.Message -eq 'accepted unsafe endpoint'){throw};'rejected'}
 }
 try{$null=New-RelayConnection ($path+'.missing') -Required;throw 'accepted missing relay'}
 catch{if($_.Exception.Message -eq 'accepted missing relay'){throw};'missing rejected'}
}finally{$first.http.Dispose()}
""".replace('STATE_PATH', state)
            self.assertEqual(self.run_ps(code), ['True'] * 4 + ['rejected'] * 3 + ['missing rejected'])

    def test_signed_reply_accepts_small_clock_skew_but_rejects_large_skew_and_expiry(self):
        now = int(time.time())
        key = bytes(range(32))
        identifier = 'a' * 32
        code = "$key=[byte[]](0..31)\n"
        for prefix, kind, status in ((bridge.SYNC_PREFIX, 'sync_result', 'synced'),
                                     (bridge.CHAT_PREFIX, 'chat_result', 'not_ready')):
            for offset in (4, 8, 60, -60):
                packet = bridge.packet(key, dict(kind=kind, id=identifier, at=now + offset, status=status), prefix)
                if kind == 'sync_result':
                    expression = f"Read-BridgeResult $key ('a'*32) '{packet}' -Sync"
                else:
                    expression = f"Read-ChatResult $key ('a'*32) '{packet}'"
                code += f"$null -ne ({expression})\n"
        self.assertEqual(self.run_ps(code), ['True', 'True', 'False', 'False'] * 2)

    def test_actual_reconnect_callback_refreshes_state_preserves_text_and_does_not_retry_pending_input(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = str(Path(directory)).replace("'", "''")
            source = str(Path(__file__).with_name('claude-input.ps1')).replace("'", "''")
            code = r"""
Add-Type -AssemblyName System.Security
$folder='FOLDER_PATH'
$keyPath=Join-Path $folder 'input-key.dpapi'
$statePath=Join-Path $folder 'relay-state.dpapi'
[IO.File]::WriteAllBytes($keyPath,[Security.Cryptography.ProtectedData]::Protect([byte[]](0..31),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser))
function Save-TestState($url){
 $bytes=[Text.Encoding]::UTF8.GetBytes((@{url=$url;token=('a'*64)}|ConvertTo-Json -Compress))
 $protected=[Security.Cryptography.ProtectedData]::Protect($bytes,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
 [IO.File]::WriteAllBytes($statePath,([Text.Encoding]::ASCII.GetBytes("HOSTCONTROL-DPAPI`n")+$protected))
}
Save-TestState 'http://127.0.0.1:31001/input-relay'
$source=Get-Content -LiteralPath 'SOURCE_PATH' -Raw -Encoding UTF8
$source=$source.Replace('Local\HostControl-Claude-Chat-Panel-v4',('Local\HostControl-Panel-Test-'+[Guid]::NewGuid().ToString('N')))
$harness=@'
$timer.Stop()
$draft.Text='kept draft'
$oldSession=$script:relay.session
Save-TestState 'http://127.0.0.1:31002/input-relay'
$resume.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
($script:relay.url -ceq 'http://127.0.0.1:31002/input-relay' -and $script:relay.session -cne $oldSession)
($draft.Text -ceq 'kept draft' -and -not $script:sync.paused -and $null -eq $script:sync.pending)
$current=$script:relay
$script:sync.pending=@{kind='submit'}
Save-TestState 'http://127.0.0.1:31003/input-relay'
$resume.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
([object]::ReferenceEquals($script:relay,$current) -and $script:sync.pending.kind -eq 'submit')
$script:sync.pending=$null
[IO.File]::Delete($statePath)
$resume.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
($script:sync.paused -and $draft.Text -ceq 'kept draft' -and $null -ne $script:relay)
$window.Close()
'@
$source=$source.Replace('[void]$window.ShowDialog()',$harness)
& ([ScriptBlock]::Create($source)) -KeyPath $keyPath
""".replace('FOLDER_PATH', folder).replace('SOURCE_PATH', source)
            self.assertEqual(self.run_ps(code), ['True'] * 4)


if __name__ == '__main__':
    unittest.main()
