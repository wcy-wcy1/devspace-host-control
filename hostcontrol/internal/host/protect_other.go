//go:build !windows

package host

import "os"

func protectDir(path string) error { return os.Chmod(path, 0700) }
