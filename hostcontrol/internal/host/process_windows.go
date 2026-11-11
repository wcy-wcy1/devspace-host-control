//go:build windows && amd64

package host

import (
	"fmt"
	"os/exec"
	"sync"
	"syscall"
	"unsafe"
)

var k32 = syscall.NewLazyDLL("kernel32.dll")
var createJob = k32.NewProc("CreateJobObjectW")
var setJob = k32.NewProc("SetInformationJobObject")
var assignJob = k32.NewProc("AssignProcessToJobObject")
var createSnapshot = k32.NewProc("CreateToolhelp32Snapshot")
var threadFirst = k32.NewProc("Thread32First")
var threadNext = k32.NewProc("Thread32Next")
var openThread = k32.NewProc("OpenThread")
var resumeThread = k32.NewProc("ResumeThread")

type jobGroup struct {
	h    syscall.Handle
	once sync.Once
}

func (g *jobGroup) Close() error {
	var e error
	g.once.Do(func() { e = syscall.CloseHandle(g.h) })
	return e
}

type threadEntry struct {
	Size, Usage, ThreadID, OwnerID uint32
	Base, Delta                    int32
	Flags                          uint32
}

// Start suspended, assign a kill-on-close job, then resume: descendants cannot
// escape in the interval between Start and assignment.
func startProcess(c *exec.Cmd) (processGroup, error) {
	h, _, e := createJob.Call(0, 0)
	if h == 0 {
		return nil, e
	}
	g := &jobGroup{h: syscall.Handle(h)}
	info := [144]byte{}
	*(*uint32)(unsafe.Pointer(&info[16])) = 0x2000
	ok, _, e := setJob.Call(h, 9, uintptr(unsafe.Pointer(&info[0])), uintptr(len(info)))
	if ok == 0 {
		g.Close()
		return nil, e
	}
	c.SysProcAttr = &syscall.SysProcAttr{CreationFlags: 0x4, HideWindow: true}
	if e := c.Start(); e != nil {
		g.Close()
		return nil, e
	}
	fail := func(err error) (processGroup, error) { _ = c.Process.Kill(); _ = c.Wait(); g.Close(); return nil, err }
	ph, e := syscall.OpenProcess(0x0100|0x0001, false, uint32(c.Process.Pid))
	if e != nil {
		return fail(e)
	}
	ok, _, e = assignJob.Call(h, uintptr(ph))
	syscall.CloseHandle(ph)
	if ok == 0 {
		return fail(e)
	}
	snap, _, e := createSnapshot.Call(0x4, 0)
	if snap == ^uintptr(0) {
		return fail(e)
	}
	defer syscall.CloseHandle(syscall.Handle(snap))
	t := threadEntry{Size: uint32(unsafe.Sizeof(threadEntry{}))}
	ok, _, _ = threadFirst.Call(snap, uintptr(unsafe.Pointer(&t)))
	resumed := false
	for ok != 0 {
		if t.OwnerID == uint32(c.Process.Pid) {
			th, _, _ := openThread.Call(0x2, 0, uintptr(t.ThreadID))
			if th != 0 {
				n, _, _ := resumeThread.Call(th)
				syscall.CloseHandle(syscall.Handle(th))
				if n != 0xffffffff {
					resumed = true
				}
			}
		}
		t.Size = uint32(unsafe.Sizeof(t))
		ok, _, _ = threadNext.Call(snap, uintptr(unsafe.Pointer(&t)))
	}
	if !resumed {
		return fail(fmt.Errorf("could not resume process"))
	}
	return g, nil
}
