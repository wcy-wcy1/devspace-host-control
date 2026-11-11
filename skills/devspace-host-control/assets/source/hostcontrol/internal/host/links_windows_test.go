//go:build windows

package host

import (
	"os"
	"os/exec"
)

func makeTestLink(target, link string) error {
	if e := os.Symlink(target, link); e == nil {
		return nil
	}
	return exec.Command("cmd.exe", "/d", "/c", "mklink", "/J", link, target).Run()
}
