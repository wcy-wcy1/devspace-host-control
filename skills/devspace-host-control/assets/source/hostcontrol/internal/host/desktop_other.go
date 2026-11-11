//go:build !windows || !amd64

package host

import "fmt"

func inspectWindow(uintptr) (any, error) { return nil, fmt.Errorf("unsupported desktop") }

func desktopAvailable() bool             { return false }
func foreground() uintptr                { return 0 }
func listWindows() (any, error)          { return nil, fmt.Errorf("unsupported desktop") }
func windowBounds(uintptr) (rect, error) { return rect{}, fmt.Errorf("unsupported desktop") }
func focusWindow(uintptr) error          { return fmt.Errorf("unsupported desktop") }
func captureWindow(uintptr) ([]byte, rect, error) {
	return nil, rect{}, fmt.Errorf("unsupported desktop")
}
func clickAt(int, int, string) error { return fmt.Errorf("unsupported desktop") }
func scrollAt(int, int, int) error   { return fmt.Errorf("unsupported desktop") }
func typeText(string) error          { return fmt.Errorf("unsupported desktop") }
func pressKeys([]string) error       { return fmt.Errorf("unsupported desktop") }
func openURL(string) error           { return fmt.Errorf("unsupported desktop") }
