//go:build windows && amd64

package host

import (
	"bytes"
	"encoding/binary"
	"image/png"
	"os"
	"testing"
	"unsafe"
)

func TestWindowsInputLayout(t *testing.T) {
	if unsafe.Sizeof(winInput{}) != 40 || unsafe.Sizeof(bitmapHeader{}) != 40 {
		t.Fatal("Windows ABI mismatch")
	}
	v := keyInput(0x11, 0, 2)
	if v.Kind != 1 || binary.LittleEndian.Uint16(v.Data[:]) != 0x11 || binary.LittleEndian.Uint32(v.Data[4:]) != 2 {
		t.Fatal("keyboard ABI mismatch")
	}
	m := mouseInput(0x0800, -120)
	if int32(binary.LittleEndian.Uint32(m.Data[8:])) != -120 || binary.LittleEndian.Uint32(m.Data[12:]) != 0x0800 {
		t.Fatal("mouse ABI mismatch")
	}
	if _, ok := virtualKey("unsupported"); ok {
		t.Fatal("invalid key accepted")
	}
}
func TestDesktopCaptureOptIn(t *testing.T) {
	if os.Getenv("HOSTCONTROL_DESKTOP_QA") != "1" {
		t.Skip("set HOSTCONTROL_DESKTOP_QA=1 for read-only live-window capture validation")
	}
	if !desktopAvailable() {
		t.Skip("no unlocked interactive desktop")
	}
	v, e := listWindows()
	if e != nil {
		t.Fatal(e)
	}
	if len(v.([]map[string]any)) == 0 {
		t.Fatal("no visible windows")
	}
	b, r, e := captureWindow(foreground())
	if e != nil {
		t.Fatal(e)
	}
	im, e := png.Decode(bytes.NewReader(b))
	if e != nil || im.Bounds().Dx() != int(r.Right-r.Left) || im.Bounds().Dy() != int(r.Bottom-r.Top) {
		t.Fatal("capture result invalid")
	}
	t.Logf("read-only capture valid: %dx%d PNG; no image saved, no input injected", im.Bounds().Dx(), im.Bounds().Dy())
}
