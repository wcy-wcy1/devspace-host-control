//go:build windows && amd64

package host

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"image"
	"image/png"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"unicode/utf16"
	"unsafe"
)

var user32 = syscall.NewLazyDLL("user32.dll")
var gdi32 = syscall.NewLazyDLL("gdi32.dll")
var getForeground = user32.NewProc("GetForegroundWindow")
var getWindowRect = user32.NewProc("GetWindowRect")
var isVisible = user32.NewProc("IsWindowVisible")
var setForeground = user32.NewProc("SetForegroundWindow")
var showWindow = user32.NewProc("ShowWindow")
var enumWindows = user32.NewProc("EnumWindows")
var getTitle = user32.NewProc("GetWindowTextW")
var getDC = user32.NewProc("GetDC")
var releaseDC = user32.NewProc("ReleaseDC")
var setCursor = user32.NewProc("SetCursorPos")
var sendInput = user32.NewProc("SendInput")
var createDC = gdi32.NewProc("CreateCompatibleDC")
var deleteDC = gdi32.NewProc("DeleteDC")
var createDIB = gdi32.NewProc("CreateDIBSection")
var deleteObject = gdi32.NewProc("DeleteObject")
var selectObject = gdi32.NewProc("SelectObject")
var bitBlt = gdi32.NewProc("BitBlt")
var openInputDesktop = user32.NewProc("OpenInputDesktop")
var closeDesktop = user32.NewProc("CloseDesktop")
var getUserObjectInfo = user32.NewProc("GetUserObjectInformationW")
var shellExecute = syscall.NewLazyDLL("shell32.dll").NewProc("ShellExecuteW")

func init() { // Physical coordinates must match screenshots even at mixed DPI.
	if p := user32.NewProc("SetProcessDpiAwarenessContext"); p.Find() == nil {
		p.Call(^uintptr(3))
	}
}
func desktopAvailable() bool {
	d, _, _ := openInputDesktop.Call(0, 0, 0x1)
	if d == 0 {
		return false
	}
	defer closeDesktop.Call(d)
	var b [256]uint16
	var needed uint32
	ok, _, _ := getUserObjectInfo.Call(d, 2, uintptr(unsafe.Pointer(&b[0])), uintptr(len(b)*2), uintptr(unsafe.Pointer(&needed)))
	return ok != 0 && strings.EqualFold(syscall.UTF16ToString(b[:]), "Default") && foreground() != 0
}
func foreground() uintptr { h, _, _ := getForeground.Call(); return h }
func windowBounds(h uintptr) (rect, error) {
	var r rect
	visible, _, _ := isVisible.Call(h)
	ok, _, _ := getWindowRect.Call(h, uintptr(unsafe.Pointer(&r)))
	if h == 0 || visible == 0 || ok == 0 || r.Right <= r.Left || r.Bottom <= r.Top {
		return r, fmt.Errorf("window unavailable")
	}
	return r, nil
}

// Go callback trampolines are never freed. Reuse one for all enumerations and
// serialize access to its result; EnumWindows invokes it synchronously.
var enumerateMu sync.Mutex
var enumeratedWindows []map[string]any
var enumerateCallback = syscall.NewCallback(func(h uintptr, _ uintptr) uintptr {
	if _, e := windowBounds(h); e == nil {
		var b [1024]uint16
		getTitle.Call(h, uintptr(unsafe.Pointer(&b[0])), uintptr(len(b)))
		title := syscall.UTF16ToString(b[:])
		if title != "" {
			enumeratedWindows = append(enumeratedWindows, map[string]any{"window_id": strconv.FormatUint(uint64(h), 10), "title": title, "foreground": h == foreground()})
		}
	}
	return 1
})

func listWindows() (any, error) {
	enumerateMu.Lock()
	defer enumerateMu.Unlock()
	enumeratedWindows = []map[string]any{}
	defer func() { enumeratedWindows = nil }()
	ok, _, err := enumWindows.Call(enumerateCallback, 0)
	if ok == 0 {
		return nil, fmt.Errorf("enumerating windows failed: %v", err)
	}
	return enumeratedWindows, nil
}
func focusWindow(h uintptr) error {
	if _, e := windowBounds(h); e != nil {
		return e
	}
	showWindow.Call(h, 9)
	setForeground.Call(h)
	if foreground() != h {
		return fmt.Errorf("Windows denied focus; focus the target locally or retry after observing")
	}
	return nil
}

type bitmapHeader struct {
	Size                   uint32
	Width, Height          int32
	Planes, BitCount       uint16
	Compression, SizeImage uint32
	XPels, YPels           int32
	Used, Important        uint32
}

func captureWindow(h uintptr) ([]byte, rect, error) {
	r, e := windowBounds(h)
	if e != nil {
		return nil, r, e
	}
	width, height := int(r.Right-r.Left), int(r.Bottom-r.Top)
	if width > 8192 || height > 8192 || int64(width)*int64(height) > 8_500_000 {
		return nil, r, fmt.Errorf("window too large; resize it before capture")
	}
	dc, _, _ := getDC.Call(0)
	if dc == 0 {
		return nil, r, fmt.Errorf("screen capture unavailable")
	}
	defer releaseDC.Call(0, dc)
	mem, _, _ := createDC.Call(dc)
	if mem == 0 {
		return nil, r, fmt.Errorf("capture DC failed")
	}
	defer deleteDC.Call(mem)
	header := bitmapHeader{Size: 40, Width: int32(width), Height: -int32(height), Planes: 1, BitCount: 32}
	var bits unsafe.Pointer
	bitmap, _, _ := createDIB.Call(dc, uintptr(unsafe.Pointer(&header)), 0, uintptr(unsafe.Pointer(&bits)), 0, 0)
	if bitmap == 0 || bits == nil {
		return nil, r, fmt.Errorf("capture allocation failed")
	}
	defer deleteObject.Call(bitmap)
	prev, _, _ := selectObject.Call(mem, bitmap)
	defer selectObject.Call(mem, prev)
	ok, _, _ := bitBlt.Call(mem, 0, 0, uintptr(width), uintptr(height), dc, uintptr(r.Left), uintptr(r.Top), 0x40CC0020)
	if ok == 0 {
		return nil, r, fmt.Errorf("screen capture failed")
	}
	pixels := unsafe.Slice((*byte)(bits), width*height*4)
	img := image.NewNRGBA(image.Rect(0, 0, width, height))
	for i := 0; i < len(pixels); i += 4 {
		img.Pix[i], img.Pix[i+1], img.Pix[i+2], img.Pix[i+3] = pixels[i+2], pixels[i+1], pixels[i], 255
	}
	var b bytes.Buffer
	if e = png.Encode(&b, img); e != nil {
		return nil, r, e
	}
	return b.Bytes(), r, nil
}

type winInput struct {
	Kind    uint32
	Padding uint32
	Data    [32]byte
}

func dispatch(inputs []winInput) error {
	if len(inputs) == 0 {
		return nil
	}
	n, _, _ := sendInput.Call(uintptr(len(inputs)), uintptr(unsafe.Pointer(&inputs[0])), unsafe.Sizeof(winInput{}))
	if n != uintptr(len(inputs)) {
		return fmt.Errorf("input partially or fully rejected; observe before retrying (UIPI/UAC may block input)")
	}
	return nil
}
func keyInput(vk, scan uint16, flags uint32) winInput {
	v := winInput{Kind: 1}
	binary.LittleEndian.PutUint16(v.Data[0:], vk)
	binary.LittleEndian.PutUint16(v.Data[2:], scan)
	binary.LittleEndian.PutUint32(v.Data[4:], flags)
	return v
}
func mouseInput(flags uint32, data int32) winInput {
	v := winInput{}
	binary.LittleEndian.PutUint32(v.Data[8:], uint32(data))
	binary.LittleEndian.PutUint32(v.Data[12:], flags)
	return v
}
func position(x, y int) error {
	ok, _, _ := setCursor.Call(uintptr(x), uintptr(y))
	if ok == 0 {
		return fmt.Errorf("cursor position rejected")
	}
	return nil
}
func clickAt(x, y int, button string) error {
	if e := position(x, y); e != nil {
		return e
	}
	down, up := uint32(2), uint32(4)
	if button == "right" {
		down, up = 8, 16
	}
	e := dispatch([]winInput{mouseInput(down, 0), mouseInput(up, 0)})
	if e != nil {
		_ = dispatch([]winInput{mouseInput(up, 0)})
	}
	return e
}
func scrollAt(x, y, steps int) error {
	if e := position(x, y); e != nil {
		return e
	}
	return dispatch([]winInput{mouseInput(0x0800, int32(steps*120))})
}
func typeText(text string) error {
	inputs := []winInput{}
	for _, r := range utf16.Encode([]rune(text)) {
		inputs = append(inputs, keyInput(0, r, 4), keyInput(0, r, 6))
	}
	return dispatch(inputs)
}
func virtualKey(s string) (uint16, bool) {
	s = strings.ToLower(s)
	keys := map[string]uint16{"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5b, "enter": 13, "tab": 9, "escape": 27, "backspace": 8, "delete": 46, "space": 32, "left": 37, "up": 38, "right": 39, "down": 40, "home": 36, "end": 35}
	if v, ok := keys[s]; ok {
		return v, true
	}
	if len(s) == 1 && s[0] >= 'a' && s[0] <= 'z' {
		return uint16(s[0] - 32), true
	}
	if len(s) == 1 && s[0] >= '0' && s[0] <= '9' {
		return uint16(s[0]), true
	}
	if strings.HasPrefix(s, "f") {
		v, e := strconv.Atoi(s[1:])
		if e == nil && v >= 1 && v <= 12 {
			return uint16(111 + v), true
		}
	}
	return 0, false
}
func pressKeys(keys []string) error {
	down := []winInput{}
	up := []winInput{}
	for _, k := range keys {
		v, ok := virtualKey(k)
		if !ok {
			return fmt.Errorf("unsupported key")
		}
		down = append(down, keyInput(v, 0, 0))
		up = append([]winInput{keyInput(v, 0, 2)}, up...)
	}
	e := dispatch(append(down, up...))
	if e != nil {
		_ = dispatch(up)
	}
	return e
}
func openURL(raw string) error {
	verb, _ := syscall.UTF16PtrFromString("open")
	u, e := syscall.UTF16PtrFromString(raw)
	if e != nil {
		return e
	}
	n, _, _ := shellExecute.Call(0, uintptr(unsafe.Pointer(verb)), uintptr(unsafe.Pointer(u)), 0, 0, 1)
	if n <= 32 {
		return fmt.Errorf("default browser launch failed")
	}
	return nil
}
