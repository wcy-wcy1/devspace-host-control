//go:build windows && !amd64

package host

import (
	"fmt"
	"os/exec"
)

func startProcess(c *exec.Cmd) (processGroup, error) {
	return nil, fmt.Errorf("Windows execution currently requires amd64")
}
