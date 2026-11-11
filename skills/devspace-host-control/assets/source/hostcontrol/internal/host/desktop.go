package host

import (
	"encoding/base64"
	"fmt"
	"net/url"
	"strconv"
	"time"
	"unicode/utf8"
)

type rect struct{ Left, Top, Right, Bottom int32 }
type frame struct {
	window        uintptr
	bounds        rect
	at            time.Time
	width, height int
}
type desktopState struct{ frames map[string]frame }

func windowID(a map[string]any) (uintptr, error) {
	s := str(a, "window_id")
	if s == "" {
		return foreground(), nil
	}
	v, e := strconv.ParseUint(s, 10, 64)
	return uintptr(v), e
}
func (s *Service) desktopCall(name string, a map[string]any) (Result, error) {
	if !desktopAvailable() {
		return Result{}, fmt.Errorf("desktop backend requires Windows amd64 and an interactive logged-in session")
	}
	switch name {
	case "desktop_windows":
		v, e := listWindows()
		return textResult(v), e
	case "desktop_inspect":
		id, e := windowID(a)
		if e != nil {
			return Result{}, fmt.Errorf("invalid window ID")
		}
		v, e := inspectWindow(id)
		return textResult(v), e
	case "desktop_focus":
		id, e := windowID(a)
		if e != nil {
			return Result{}, fmt.Errorf("invalid window ID")
		}
		if e = focusWindow(id); e != nil {
			return Result{}, e
		}
		return textResult(map[string]any{"focused": true}), nil
	case "browser_open":
		u, e := url.Parse(str(a, "url"))
		if e != nil || u.Host == "" || u.User != nil || u.Scheme != "http" && u.Scheme != "https" {
			return Result{}, fmt.Errorf("explicit HTTP/HTTPS URL required")
		}
		if e = openURL(u.String()); e != nil {
			return Result{}, e
		}
		return textResult(map[string]any{"opened": true}), nil
	case "desktop_capture":
		id, e := windowID(a)
		if e != nil {
			return Result{}, fmt.Errorf("invalid window ID")
		}
		b, r, e := captureWindow(id)
		if e != nil {
			return Result{}, e
		}
		if len(b) > 4<<20 {
			return Result{}, fmt.Errorf("image exceeds 4 MiB; use a smaller window")
		}
		if s.desktop.frames == nil {
			s.desktop.frames = map[string]frame{}
		}
		for k, v := range s.desktop.frames {
			if time.Since(v.at) > 30*time.Second {
				delete(s.desktop.frames, k)
			}
		}
		if len(s.desktop.frames) >= 8 {
			s.desktop.frames = map[string]frame{}
		}
		fid := randomID()
		s.desktop.frames[fid] = frame{id, r, time.Now(), int(r.Right - r.Left), int(r.Bottom - r.Top)}
		result := textResult(map[string]any{"frame_id": fid, "window_id": strconv.FormatUint(uint64(id), 10), "width": r.Right - r.Left, "height": r.Bottom - r.Top, "expires_seconds": 30, "capture_mode": "visible-screen-region-may-include-overlays"})
		result.Content = append(result.Content, Content{Type: "image", MimeType: "image/png", Data: base64.StdEncoding.EncodeToString(b)})
		return result, nil
	}
	f, ok := s.desktop.frames[str(a, "frame_id")]
	if !ok || time.Since(f.at) > 30*time.Second {
		return Result{}, fmt.Errorf("frame missing or expired; capture again")
	}
	r, e := windowBounds(f.window)
	if e != nil || r != f.bounds || foreground() != f.window {
		return Result{}, fmt.Errorf("window moved, lost focus, or is unavailable; focus and capture again")
	}
	// Consume before dispatch, including failures: never replay an input after an
	// uncertain partial outcome. The caller must observe a new frame.
	delete(s.desktop.frames, str(a, "frame_id"))
	switch name {
	case "desktop_click", "desktop_scroll":
		xf, xok := a["x"].(float64)
		yf, yok := a["y"].(float64)
		x, y := int(xf), int(yf)
		if !xok || !yok || float64(x) != xf || float64(y) != yf || x < 0 || y < 0 || x >= f.width || y >= f.height {
			return Result{}, fmt.Errorf("coordinates outside frame")
		}
		if name == "desktop_click" {
			button := str(a, "button")
			if button == "" {
				button = "left"
			}
			if button != "left" && button != "right" {
				return Result{}, fmt.Errorf("button must be left or right")
			}
			e = clickAt(int(r.Left)+x, int(r.Top)+y, button)
		} else {
			steps := number(a, "steps", 0)
			if steps == 0 || steps < -12 || steps > 12 {
				return Result{}, fmt.Errorf("steps must be -12..12, excluding zero")
			}
			e = scrollAt(int(r.Left)+x, int(r.Top)+y, steps)
		}
	case "desktop_type":
		text := str(a, "text")
		if !utf8.ValidString(text) || utf8.RuneCountInString(text) > 4000 {
			return Result{}, fmt.Errorf("text must be valid Unicode, maximum 4000 code points")
		}
		e = typeText(text)
	case "desktop_key":
		raw, ok := a["keys"].([]any)
		if !ok || len(raw) == 0 || len(raw) > 8 {
			return Result{}, fmt.Errorf("keys must contain 1..8 key names")
		}
		keys := []string{}
		for _, v := range raw {
			k, ok := v.(string)
			if !ok {
				return Result{}, fmt.Errorf("invalid key")
			}
			keys = append(keys, k)
		}
		e = pressKeys(keys)
	default:
		return Result{}, fmt.Errorf("unknown desktop tool")
	}
	if e != nil {
		return Result{}, e
	}
	return textResult(map[string]any{"performed": true, "next": "capture again before the next input"}), nil
}
