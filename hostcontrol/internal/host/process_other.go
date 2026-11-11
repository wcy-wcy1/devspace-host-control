//go:build !windows && !linux

package host

import (
	"fmt"
	"os/exec"
)

func startProcess(c *exec.Cmd) (processGroup, error) {
	return nil, fmt.Errorf("process backend supports Windows and Linux only")
}
