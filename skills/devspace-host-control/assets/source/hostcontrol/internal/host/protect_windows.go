//go:build windows

package host

import (
	"fmt"
	"os/exec"
	"os/user"
)

func protectDir(path string) error {
	u, e := user.Current()
	if e != nil {
		return e
	}
	b, e := exec.Command("icacls.exe", path, "/inheritance:r", "/grant:r", "*"+u.Uid+":(OI)(CI)F").CombinedOutput()
	if e != nil {
		return fmt.Errorf("credential directory ACL failed: %s", b)
	}
	return nil
}
