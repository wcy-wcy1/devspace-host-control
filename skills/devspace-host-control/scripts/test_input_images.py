import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

import input_bridge as bridge


class ImageNode:
    def __init__(self, role='image', children=(), width=200, height=120, name='landscape'):
        self.role, self.children = role, children
        self.width, self.height, self.name = width, height, name

    def get_role_name(self): return self.role
    def get_child_count(self): return len(self.children)
    def get_child_at_index(self, index): return self.children[index]
    def get_name(self): return self.name
    def get_component_iface(self): return self
    def get_extents(self, coord): return self


class ImageBridgeTests(unittest.TestCase):
    def fixture(self, capture=lambda n: {'preview': True, 'mime': 'image/png'}):
        prompt, panel, anchor, reply = (object() for _ in range(4))
        image = ImageNode()
        view = {'panel': panel, 'busy': False, 'turns': [
            dict(node=anchor, author='user', text='draw'),
            dict(node=reply, author='claude', text='result', complete=True, truncated=False, images=[image])]}
        watch = bridge.ReplyWatch(lambda: [prompt], lambda n: view,
                                 lambda *args: self.fail('Read must not submit'), capture)
        response = watch.recover(dict(id='a' * 32, stream='b' * 32, text='draw'))
        message = dict(kind='image', id='c' * 32, stream='b' * 32, turn='a' * 32,
                       image=response['images'][0]['id'], at=int(time.time()))
        return watch, view, image, message

    def test_content_images_exclude_icons_controls_and_limit_count(self):
        images = [ImageNode() for _ in range(8)]
        icon = ImageNode(width=24, height=24)
        root = ImageNode('article', [ImageNode('tool bar', [ImageNode()]),
                         ImageNode('push button', [ImageNode()]), icon, *images])
        self.assertEqual(bridge.message_images(root), images[:4])

    def test_scoped_preview_reads_only_bound_image_and_rejects_new_turn_or_token(self):
        captures = []
        watch, view, image, message = self.fixture(lambda n: captures.append(n) or {'preview': True})
        self.assertEqual(watch.image(message)['status'], 'image_ready')
        self.assertEqual(captures, [image])
        for invalid in (dict(message, image='d' * 32), dict(message, turn='d' * 32),
                        dict(message, stream='d' * 32)):
            self.assertEqual(watch.image(invalid)['status'], 'image_unavailable')
        view['turns'].append(dict(node=object(), author='user', text='next'))
        self.assertEqual(watch.image(message)['status'], 'image_unavailable')
        self.assertEqual(captures, [image])

    def test_navigation_during_capture_discards_pixels(self):
        watch, view, image, message = self.fixture()
        def capture(node):
            view['panel'] = object()
            return {'data': 'must not leave receiver'}
        watch.capture = capture
        self.assertEqual(watch.image(message), {'status': 'image_unavailable'})

    def test_removed_or_unavailable_images_never_return_previous_pixels(self):
        watch, view, image, message = self.fixture()
        def unavailable(node): raise ValueError('off screen')
        watch.capture = unavailable
        self.assertEqual(watch.image(message), {'status': 'image_unavailable'})
        view['turns'][-1]['images'] = []
        self.assertEqual(watch.image(message), {'status': 'image_unavailable'})

    def test_preview_protocol_requires_signed_scoped_ids_and_does_not_fill(self):
        watch, view, image, message = self.fixture()
        key = bytes(range(32))
        raw = bridge.packet(key, message, bridge.CHAT_PREFIX)
        self.assertEqual(bridge.unpack(key, raw), message)
        for invalid in (dict(message, image='https://example.com/x.png'),
                        dict(message, image='/etc/passwd'), dict(message, turn='../'),
                        dict(message, at=0)):
            with self.assertRaises(ValueError):
                bridge.unpack(key, bridge.packet(key, invalid, bridge.CHAT_PREFIX))
        with self.assertRaises(ValueError): bridge.unpack(b'x' * 32, raw)
        with tempfile.TemporaryDirectory() as folder:
            def forbidden(*args): self.fail('Image read must not fill/sync/submit')
            receiver = bridge.Receiver(key, Path(folder) / 'seen.json', forbidden, forbidden, watch)
            result = receiver.receive(raw)
            outer = json.loads(base64.b64decode(result[len(bridge.CHAT_PREFIX):]))
            self.assertEqual(json.loads(base64.b64decode(outer['body']))['status'], 'image_ready')
            self.assertIsNone(receiver.last_text)
            self.assertIsNone(receiver.receive(raw))
            self.assertNotIn('data', (Path(folder) / 'seen.json').read_text())

    def test_maximum_image_packet_fits_existing_transport_without_text_truncation(self):
        image = dict(id='a' * 32, mime='image/png', data=base64.b64encode(b'x' * bridge.MAX_IMAGE_BYTES).decode(),
                     width=1024, height=1024, preview=True)
        result = bridge.packet(bytes(32), dict(kind='chat_result', id='b' * 32, at=1,
                               status='image_ready', image=image), bridge.CHAT_PREFIX)
        self.assertLess(len(result), bridge.MAX_PACKET)

    def test_capture_bounds_reject_partial_oversized_and_adjacent_regions(self):
        self.assertTrue(bridge.contained_rect((100, 100, 690, 470), (0, 0, 1100, 800)))
        for bounds in ((100, -6, 690, 470), (-20, 100, 690, 470), (900, 100, 690, 470),
                       (100, 700, 690, 470), (0, 0, 8000, 8000), (100, 100, 20, 20)):
            self.assertFalse(bridge.contained_rect(bounds, (0, 0, 1100, 800)))


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows WPF')
class ImagePanelTests(unittest.TestCase):
    def run_ps(self, code):
        source = Path(__file__).with_name('claude-input.ps1')
        code = ". '" + str(source).replace("'", "''") + "' -FunctionsOnly\n" + code
        result = subprocess.run(['powershell.exe', '-NoProfile', '-STA', '-EncodedCommand',
                                 base64.b64encode(code.encode('utf-16-le')).decode()],
                                capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip().splitlines()

    def test_png_decode_rejects_urls_wrong_type_dimensions_and_corruption(self):
        self.assertEqual(self.run_ps(r"""
Add-Type -AssemblyName PresentationFramework
$bitmap=[Windows.Media.Imaging.RenderTargetBitmap]::new(200,120,96,96,[Windows.Media.PixelFormats]::Pbgra32)
$encoder=[Windows.Media.Imaging.PngBitmapEncoder]::new();$encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($bitmap))
$memory=[IO.MemoryStream]::new();$encoder.Save($memory)
$image=@{id=('a'*32);mime='image/png';width=200;height=120;preview=$true;data=[Convert]::ToBase64String($memory.ToArray())}
(Test-ImagePreview $image)
$decoded=ConvertTo-PreviewBitmap $image
($decoded.PixelWidth -eq 200 -and $decoded.PixelHeight -eq 120 -and $decoded.IsFrozen)
foreach($bad in @(@{data='https://example.com/x.png'},@{mime='image/svg+xml'},@{width=999999},@{preview=$false},@{id='../'})){
 $copy=$image.Clone();foreach($k in $bad.Keys){$copy[$k]=$bad[$k]};(-not (Test-ImagePreview $copy))
}
$image.data=[Convert]::ToBase64String($memory.ToArray()[0..32])
try{$null=ConvertTo-PreviewBitmap $image;'bad accepted'}catch{'corrupt rejected'}
$memory.Dispose()
"""), ['True'] * 7 + ['corrupt rejected'])

    def test_signed_image_request_matches_python_and_tampered_preview_is_rejected(self):
        lines = self.run_ps(r"""
$key=[byte[]](0..31)
New-BridgePacket $key ('a'*32) '' ('b'*32) 1 '' 'image' ('c'*32) ('d'*32)
""")
        value = bridge.unpack(bytes(range(32)), lines[0])
        self.assertEqual((value['kind'], value['turn'], value['image']), ('image', 'c' * 32, 'd' * 32))
        raw = bridge.packet(bytes(range(32)), dict(kind='chat_result', id='a' * 32, at=int(time.time()),
                            status='image_ready', image={'data': 'not png'}), bridge.CHAT_PREFIX)
        self.assertEqual(self.run_ps("$null -eq (Read-ChatResult ([byte[]](0..31)) ('a'*32) '" + raw + "')"), ['True'])

    def test_real_xaml_image_card_and_scroll_viewport_at_minimum_and_normal_sizes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = str(Path(__file__).with_name('claude-input.ps1')).replace("'", "''")
            folder = directory.replace("'", "''")
            code = r"""
Add-Type -AssemblyName System.Security
$keyPath='FOLDER/input-key.dpapi'
[IO.File]::WriteAllBytes($keyPath,[Security.Cryptography.ProtectedData]::Protect([byte[]](0..31),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser))
$source=Get-Content -LiteralPath 'SOURCE' -Raw -Encoding UTF8
$source=$source.Replace('Local\HostControl-Claude-Chat-Panel-v4',('Local\HostControl-Image-Test-'+[Guid]::NewGuid().ToString('N')))
$harness=@'
$timer.Stop()
$bitmap=[Windows.Media.Imaging.RenderTargetBitmap]::new(690,470,96,96,[Windows.Media.PixelFormats]::Pbgra32);$bitmap.Freeze()
$script:sync.imageResults['a']=@{bitmap=$bitmap;label='<script>not markup</script>'}
Show-ReplyImages
($replyImages.Children.Count -eq 1)
($replyImages.Children[0].Child.Children[1].Text -ceq '<script>not markup</script> · 预览截图')
$reply.Text=('long reply'+"`n")*100
$draft.Text='kept draft'
$content=$window.Content;$window.Content=$null
$frame=[Windows.Controls.Border]::new();$frame.Resources=$window.Resources;$frame.Child=$content
foreach($width in @(660,840)){
 $size=[Windows.Size]::new($width,800);$frame.Measure($size);$frame.Arrange([Windows.Rect]::new($size));$frame.UpdateLayout()
 foreach($name in @('ReplyScroll','Draft','Send','Resume','RecoverReply')){
  $control=$window.FindName($name);$point=$control.TranslatePoint([Windows.Point]::new(0,0),$frame)
  if($control.ActualWidth -le 0 -or $control.ActualHeight -le 0 -or $point.Y+$control.ActualHeight -gt 801 -or $point.X+$control.ActualWidth -gt $width+1){throw 'clipped viewport/control'}
 }
}
'layout passed'
Reset-ReplyImages
($replyImages.Children.Count -eq 0 -and $draft.Text -ceq 'kept draft' -and $null -eq $script:sync.pending)
$script:relay=@{opened=$true}
function Invoke-RelayLocal($Body) {}
$script:sync.sentText='draw'
$recoverReply.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
if($script:sync.pending.kind -ne 'recover'){throw $status.Text}
($script:sync.pending.text -ceq 'draw' -and $draft.Text -ceq 'kept draft')
$script:sync.pending=$null;$script:relay.opened=$false
function Invoke-RelayLocal($Body){$failure=[InvalidOperationException]::new('busy');$failure.Data['relay_busy']=$true;throw $failure}
Start-DraftSync 'recover'
($status.Text.Contains('占用') -and $draft.Text -ceq 'kept draft' -and $null -eq $script:sync.pending)
$script:relay=$null
$window.Close()
'@
$source=$source.Replace('[void]$window.ShowDialog()',$harness)
& ([ScriptBlock]::Create($source)) -KeyPath $keyPath
""".replace('FOLDER', folder).replace('SOURCE', source)
            self.assertEqual(self.run_ps(code), ['True', 'True', 'layout passed', 'True', 'True', 'True'])


if __name__ == '__main__':
    unittest.main()
