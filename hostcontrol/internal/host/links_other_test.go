//go:build !windows

package host

import "os"

func makeTestLink(target, link string) error { return os.Symlink(target, link) }
