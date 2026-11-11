//go:build !windows

package host

import "os"

func protectKey(b []byte) ([]byte, error)        { return b, nil }
func loadPrivateKey(path string) ([]byte, error) { return os.ReadFile(path) }
