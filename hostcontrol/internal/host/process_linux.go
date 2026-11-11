//go:build linux

package host

import (
	"os/exec"
	"sync"
	"syscall"
)

type unixGroup struct {
	pid  int
	once sync.Once
}

func (g *unixGroup) Close() error {
	var e error
	g.once.Do(func() { e = syscall.Kill(-g.pid, syscall.SIGKILL) })
	return e
}
func startProcess(c *exec.Cmd) (processGroup, error) {
	c.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if e := c.Start(); e != nil {
		return nil, e
	}
	return &unixGroup{pid: c.Process.Pid}, nil
}
