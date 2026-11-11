package host

import (
	"bytes"
	"fmt"
	"os/exec"
	"sync"
	"time"
)

type limitedOutput struct {
	mu        sync.Mutex
	b         bytes.Buffer
	truncated bool
}

func (o *limitedOutput) Write(p []byte) (int, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	n := len(p)
	left := (256 << 10) - o.b.Len()
	if left > 0 {
		if len(p) > left {
			p = p[:left]
		}
		o.b.Write(p)
	}
	if n > left {
		o.truncated = true
	}
	return n, nil
}
func (o *limitedOutput) state() (string, bool) {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.b.String(), o.truncated
}

type processGroup interface{ Close() error }
type Task struct {
	mu             sync.Mutex
	id             string
	cmd            *exec.Cmd
	group          processGroup
	stdout, stderr limitedOutput
	done           chan struct{}
	finished       bool
	code           int
	reason         string
	started        time.Time
}

func (t *Task) cancel(reason string) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.finished {
		return
	}
	if t.reason == "" {
		t.reason = reason
	}
	if t.group != nil {
		_ = t.group.Close()
	}
}
func (t *Task) state() map[string]any {
	t.mu.Lock()
	done, code, reason := t.finished, t.code, t.reason
	t.mu.Unlock()
	out, ot := t.stdout.state()
	err, et := t.stderr.state()
	return map[string]any{"task_id": t.id, "finished": done, "exit_code": code, "reason": reason, "stdout": out, "stderr": err, "truncated": ot || et}
}

func (s *Service) taskCall(name string, a map[string]any) (any, error) {
	if name == "exec_start" {
		program := str(a, "program")
		if program == "" || len(program) > 4096 {
			return nil, fmt.Errorf("program required")
		}
		raw, ok := a["args"].([]any)
		if !ok {
			return nil, fmt.Errorf("args must be an array")
		}
		if len(raw) > 256 {
			return nil, fmt.Errorf("too many arguments")
		}
		args := []string{}
		for _, v := range raw {
			q, ok := v.(string)
			if !ok || len(q) > 65536 {
				return nil, fmt.Errorf("invalid argument")
			}
			args = append(args, q)
		}
		timeout := number(a, "timeout_seconds", 120)
		if timeout < 1 || timeout > 3600 {
			return nil, fmt.Errorf("timeout_seconds must be 1..3600")
		}
		cwd, e := s.execCWD(a)
		if e != nil {
			return nil, e
		}
		s.tasksMu.Lock()
		defer s.tasksMu.Unlock()
		active := 0
		for id, t := range s.tasks {
			t.mu.Lock()
			done := t.finished
			t.mu.Unlock()
			if !done {
				active++
			} else if time.Since(t.started) > time.Hour {
				delete(s.tasks, id)
			}
		}
		if active >= 4 || len(s.tasks) >= 64 {
			return nil, fmt.Errorf("task capacity reached")
		}
		t := &Task{id: randomID(), cmd: exec.Command(program, args...), done: make(chan struct{}), code: -1, started: time.Now()}
		t.cmd.Dir = cwd
		t.cmd.Stdout = &t.stdout
		t.cmd.Stderr = &t.stderr
		g, e := startProcess(t.cmd)
		if e != nil {
			return nil, fmt.Errorf("process start failed")
		}
		t.group = g
		s.tasks[t.id] = t
		go func() {
			e := t.cmd.Wait()
			t.mu.Lock()
			_ = t.group.Close()
			t.finished = true
			t.code = 0
			if e != nil {
				t.code = t.cmd.ProcessState.ExitCode()
			}
			t.mu.Unlock()
			close(t.done)
		}()
		go func() {
			timer := time.NewTimer(time.Duration(timeout) * time.Second)
			defer timer.Stop()
			select {
			case <-timer.C:
				t.cancel("timeout")
			case <-t.done:
			}
		}()
		return map[string]any{"task_id": t.id}, nil
	}
	s.tasksMu.Lock()
	t := s.tasks[str(a, "task_id")]
	s.tasksMu.Unlock()
	if t == nil {
		return nil, fmt.Errorf("unknown task")
	}
	if name == "exec_cancel" {
		t.cancel("cancelled")
	}
	return t.state(), nil
}
func (s *Service) CancelTasks(reason string) {
	s.tasksMu.Lock()
	defer s.tasksMu.Unlock()
	for _, t := range s.tasks {
		t.cancel(reason)
	}
}
