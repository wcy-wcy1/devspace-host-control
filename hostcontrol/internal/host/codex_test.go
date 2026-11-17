package host

import (
	"archive/tar"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"image"
	"image/png"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func codexFixtureConfig(t *testing.T) CodexConfig {
	t.Helper()
	program, e := os.Executable()
	if e != nil {
		t.Fatal(e)
	}
	jobs, e := filepath.EvalSymlinks(t.TempDir())
	if e != nil {
		t.Fatal(e)
	}
	auth, e := filepath.EvalSymlinks(t.TempDir())
	if e != nil {
		t.Fatal(e)
	}
	if e := os.WriteFile(filepath.Join(auth, "auth.json"), []byte(`{"OPENAI_API_KEY":"fixture-not-a-real-secret"}`), 0600); e != nil {
		t.Fatal(e)
	}
	return CodexConfig{Directory: jobs, Git: program, Docker: program, Image: "example/runner@sha256:" + strings.Repeat("a", 64), AuthDirectory: auth, TimeoutSeconds: 60, MaxRounds: 3, Projects: map[string]CodexProject{"app": {Repository: "https://github.com/example/app", Refs: []string{"refs/heads/claude/test"}, Modes: []string{"test", "review", "implement"}, Suites: map[string][]string{"unit": {"node", "--test"}}}}}
}
func codexFixtureService(t *testing.T) (*Service, *atomic.Int32) {
	t.Helper()
	s := testService(t)
	cfg := codexFixtureConfig(t)
	m, e := newCodexManager(cfg)
	if e != nil {
		t.Fatal(e)
	}
	s.codex = m
	s.cfg.Codex = &cfg
	s.cfg.Capabilities = []string{"files", "codex"}
	count := &atomic.Int32{}
	m.run = func(ctx context.Context, dir, program string, argv []string, input string) ([]byte, error) {
		if contains(argv, "archive") {
			for _, arg := range argv {
				if strings.HasPrefix(arg, "--output=") {
					var data bytes.Buffer
					w := tar.NewWriter(&data)
					body := []byte("original\n")
					_ = w.WriteHeader(&tar.Header{Name: "app.txt", Mode: 0644, Size: int64(len(body))})
					_, _ = w.Write(body)
					_ = w.Close()
					return nil, os.WriteFile(strings.TrimPrefix(arg, "--output="), data.Bytes(), 0600)
				}
			}
		}
		if len(argv) > 0 && argv[0] == "run" {
			count.Add(1)
			id := ""
			for i, a := range argv {
				if a == "--name" {
					id = strings.TrimPrefix(argv[i+1], "hostcontrol-codex-")
				}
			}
			var request codexRequest
			if err := ReadJSON(m.path(id, "state.json"), new(codexJob)); err != nil {
				return nil, err
			}
			m.mu.Lock()
			request = m.jobs[id].Request
			m.mu.Unlock()
			if request.Mode == "implement" {
				_ = os.WriteFile(m.path(id, "workspace", "app.txt"), []byte("modified\n"), 0600)
				_ = os.WriteFile(m.path(id, "artifacts", "changes.patch"), []byte("fixture patch"), 0600)
			}
			report := map[string]any{"version": 1, "commit": request.Commit, "agent_completed": true, "summary": "fixture report", "tests": map[string]any{"passed": false, "exit_code": 1}}
			data, _ := json.Marshal(report)
			if e := os.WriteFile(m.path(id, "artifacts", "report.json"), data, 0600); e != nil {
				return nil, e
			}
			var pngData bytes.Buffer
			_ = png.Encode(&pngData, image.NewRGBA(image.Rect(0, 0, 3, 2)))
			_ = os.WriteFile(m.path(id, "artifacts", "screen.png"), pngData.Bytes(), 0600)
			_ = os.WriteFile(m.path(id, "artifacts", "report.pdf"), []byte("%PDF-1.7\n\x00fixture"), 0600)
		}
		return nil, nil
	}
	if err := s.Enable(1); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		s.Pause(false)
		m.mu.Lock()
		jobs := []*codexJob{}
		for _, j := range m.jobs {
			jobs = append(jobs, j)
		}
		m.mu.Unlock()
		for _, j := range jobs {
			select {
			case <-j.done:
			case <-time.After(5 * time.Second):
				t.Error("job did not stop")
			}
		}
	})
	return s, count
}
func codexStartArgs(id, mode string) map[string]any {
	a := args("request_id", id, "project", "app", "ref", "refs/heads/claude/test", "commit", strings.Repeat("b", 40), "mode", mode, "instruction", "inspect fixture")
	if mode == "test" {
		a["suite"] = "unit"
	}
	return a
}
func waitCodex(t *testing.T, s *Service, id string) map[string]any {
	t.Helper()
	s.codex.mu.Lock()
	job := s.codex.jobs[id]
	s.codex.mu.Unlock()
	if job == nil {
		t.Fatal("job not found")
	}
	select {
	case <-job.done:
	case <-time.After(10 * time.Second):
		t.Fatal("job timeout")
	}
	return decodeResult(t, invoke(s, "codex_status", args("job_id", id)))
}

func TestCodexCapabilityDefaultOffAndDiscovery(t *testing.T) {
	s := testService(t)
	if !invoke(s, "codex_start", codexStartArgs(strings.Repeat("a", 32), "test")).IsError {
		t.Fatal("disabled Codex accepted")
	}
	responses := mcpResponses(t, serviceCaller(s), map[string]any{"method": "tools/list"})
	for _, tool := range responses[0]["result"].(map[string]any)["tools"].([]any) {
		if strings.HasPrefix(tool.(map[string]any)["name"].(string), "codex_") {
			t.Fatal("disabled tools advertised")
		}
	}
	s, _ = codexFixtureService(t)
	responses = mcpResponses(t, serviceCaller(s), map[string]any{"method": "tools/list"})
	count := 0
	for _, tool := range responses[0]["result"].(map[string]any)["tools"].([]any) {
		if strings.HasPrefix(tool.(map[string]any)["name"].(string), "codex_") {
			count++
		}
	}
	if count != 7 {
		t.Fatal("expected seven Codex tools", count)
	}
	projects := decodeResult(t, invoke(s, "codex_projects", nil))
	encoded, _ := json.Marshal(projects)
	if strings.Contains(string(encoded), s.cfg.Codex.Directory) || strings.Contains(string(encoded), "repository") {
		t.Fatal("private host configuration exposed")
	}
	s.Pause(false)
	if !invoke(s, "codex_start", codexStartArgs(strings.Repeat("a", 32), "test")).IsError {
		t.Fatal("paused start accepted")
	}
}
func TestCodexConfigurationRequiresExplicitIsolation(t *testing.T) {
	s := testService(t)
	s.cfg.Listen = "100.100.100.10:7443"
	s.cfg.PeerIP = "100.100.100.11"
	s.cfg.CA, s.cfg.Cert = "fixture-ca.pem", "fixture-cert.pem"
	cfg := codexFixtureConfig(t)
	s.cfg.Codex = &cfg
	if s.cfg.Validate() == nil {
		t.Fatal("config silently opted in")
	}
	s.cfg.Capabilities = append(s.cfg.Capabilities, "codex")
	if e := s.cfg.Validate(); e != nil {
		t.Fatal(e)
	}
	for _, change := range []func(*CodexConfig){func(c *CodexConfig) { c.Image = "runner:latest" }, func(c *CodexConfig) { c.AuthDirectory = c.Directory }, func(c *CodexConfig) { c.Directory = s.cfg.Roots["work"] }, func(c *CodexConfig) { c.MaxRounds = 99 }, func(c *CodexConfig) { c.Docker = "docker" }, func(c *CodexConfig) {
		c.Projects = map[string]CodexProject{"app": {Repository: "https://token@example.com/app", Refs: []string{"refs/heads/main"}, Modes: []string{"review"}}}
	}} {
		copy := cfg
		change(&copy)
		s.cfg.Codex = &copy
		if s.cfg.Validate() == nil {
			t.Fatal("unsafe config accepted")
		}
	}
}
func TestCodexRejectsRemotePolicyChangesBeforeSideEffects(t *testing.T) {
	s, count := codexFixtureService(t)
	for _, change := range []map[string]any{{"project": "unknown"}, {"ref": "main; echo hacked"}, {"commit": "HEAD"}, {"mode": "shell"}, {"suite": "unknown"}, {"instruction": ""}, {"program": "powershell.exe"}, {"repository": "https://other/repo"}, {"path": "C:/Users"}} {
		a := codexStartArgs(randomID(), "test")
		for key, value := range change {
			a[key] = value
		}
		if !invoke(s, "codex_start", a).IsError {
			t.Fatal("unsafe arguments accepted", change)
		}
	}
	if count.Load() != 0 || len(s.codex.jobs) != 0 {
		t.Fatal("invalid request caused job execution")
	}
}
func TestCodexJobIdempotencyArtifactsAndExplicitContinuation(t *testing.T) {
	s, count := codexFixtureService(t)
	id := randomID()
	a := codexStartArgs(id, "implement")
	decodeResult(t, invoke(s, "codex_start", a))
	decodeResult(t, invoke(s, "codex_start", a))
	state := waitCodex(t, s, id)
	if state["state"] != "completed" || count.Load() != 1 {
		t.Fatal(state, count.Load())
	}
	a["instruction"] = "different"
	if !invoke(s, "codex_start", a).IsError {
		t.Fatal("request ID reused with new task")
	}
	result := decodeResult(t, invoke(s, "codex_result", args("job_id", id)))
	report := result["report"].(map[string]any)
	if report["tests"].(map[string]any)["passed"] != false {
		t.Fatal("completion conflated with successful tests")
	}
	for _, artifact := range result["artifacts"].([]any) {
		item := artifact.(map[string]any)
		r := invoke(s, "codex_artifact_get", args("job_id", id, "artifact_id", item["artifact_id"].(string)))
		if r.IsError || len(r.Content) != 2 {
			t.Fatal(r)
		}
		if item["name"] == "screen.png" {
			c := r.Content[1]
			if c.Type != "image" || c.MimeType != "image/png" {
				t.Fatal("image not returned as MCP image")
			}
			b, e := base64.StdEncoding.DecodeString(c.Data)
			if e != nil {
				t.Fatal(e)
			}
			cfg, _, e := image.DecodeConfig(bytes.NewReader(b))
			if e != nil || cfg.Width != 3 {
				t.Fatal("invalid image result")
			}
		}
		if item["name"] == "report.pdf" && r.Content[1].Resource.Blob == "" {
			t.Fatal("binary resource missing")
		}
	}
	if !invoke(s, "codex_artifact_get", args("job_id", id, "artifact_id", "../../secret")).IsError {
		t.Fatal("arbitrary path accepted")
	}
	child := randomID()
	s.codex.cfg.MaxRounds = 2
	decodeResult(t, invoke(s, "codex_continue", args("request_id", child, "job_id", id, "instruction", "review changes again")))
	state = waitCodex(t, s, child)
	if state["state"] != "completed" || state["round"] != float64(2) || state["commit"] != strings.Repeat("b", 40) {
		t.Fatal(state)
	}
	baseline, _ := os.ReadFile(s.codex.path(child, "baseline", "app.txt"))
	if string(baseline) != "original\n" {
		t.Fatal("continuation lost original base")
	}
	if !invoke(s, "codex_continue", args("request_id", randomID(), "job_id", child, "instruction", "exceed round limit")).IsError {
		t.Fatal("continuation exceeded locally approved round limit")
	}
	// Registered artifacts are immutable: file replacement invalidates reads.
	_ = os.WriteFile(s.codex.path(id, "artifacts", "report.json"), []byte("changed"), 0600)
	if !invoke(s, "codex_result", args("job_id", id)).IsError {
		t.Fatal("changed report accepted")
	}
	if count.Load() != 2 {
		t.Fatal("unexpected worker count", count.Load())
	}
}
func TestCodexCancellationKillsContainerAndRefusesReplayAfterRestart(t *testing.T) {
	s, _ := codexFixtureService(t)
	original := s.codex.run
	entered := make(chan struct{})
	removed := &atomic.Int32{}
	s.codex.run = func(ctx context.Context, dir, program string, argv []string, input string) ([]byte, error) {
		if argv[0] == "run" {
			close(entered)
			<-ctx.Done()
			return nil, ctx.Err()
		}
		if contains(argv, "rm") {
			removed.Add(1)
		}
		return original(ctx, dir, program, argv, input)
	}
	id := randomID()
	decodeResult(t, invoke(s, "codex_start", codexStartArgs(id, "test")))
	<-entered
	s.Pause(false)
	if err := s.Enable(1); err != nil {
		t.Fatal(err)
	}
	state := waitCodex(t, s, id)
	if state["state"] != "cancelled" || removed.Load() == 0 {
		t.Fatal("container not cancelled", state)
	}
	// Persist an interrupted operation, then reconstruct a manager as on restart.
	s.codex.mu.Lock()
	j := s.codex.jobs[id]
	j.State = "running"
	if e := s.codex.save(j); e != nil {
		t.Fatal(e)
	}
	s.codex.mu.Unlock()
	m, e := newCodexManager(s.codex.cfg)
	if e != nil {
		t.Fatal(e)
	}
	m.run = original
	r, e := m.call("codex_start", codexStartArgs(id, "test"))
	if e != nil {
		t.Fatal(e)
	}
	v := decodeResult(t, r)
	if v["state"] != "cleanup_required" {
		t.Fatal("interrupted work replayed", v)
	}
	if _, e := m.call("codex_start", codexStartArgs(randomID(), "test")); e == nil {
		t.Fatal("new work accepted before cleanup")
	}
	if _, e := m.call("codex_cancel", args("job_id", id)); e != nil {
		t.Fatal(e)
	}
	if m.jobs[id].State != "cancelled" {
		t.Fatal("cleanup not recorded")
	}
}
func TestCodexDockerBoundaryHasOnlyFixedMountsAndReadOnlyTestSource(t *testing.T) {
	cfg := codexFixtureConfig(t)
	m := &codexManager{cfg: cfg}
	j := &codexJob{ID: randomID(), Request: codexRequest{Mode: "test"}}
	argv := m.dockerArgs(j)
	all := strings.Join(argv, " ")
	for _, required := range []string{"--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pull=never", "--pids-limit=256", ",dst=/workspace,readonly", ",dst=/auth/auth.json,readonly"} {
		if !strings.Contains(all, required) {
			t.Fatal("missing boundary", required)
		}
	}
	for _, forbidden := range []string{"--privileged", "docker.sock", "--network=host", "OPENAI_API_KEY", "--pid=host"} {
		if strings.Contains(all, forbidden) {
			t.Fatal("unsafe Docker arguments", all)
		}
	}
	j.Request.Mode = "implement"
	all = strings.Join(m.dockerArgs(j), " ")
	if strings.Contains(all, ",dst=/workspace,readonly") {
		t.Fatal("implementation snapshot remains read-only")
	}
}
func TestCodexSourceArchiveRejectsLinksTraversalAndAmbiguousPaths(t *testing.T) {
	for _, header := range []*tar.Header{{Name: "../outside", Mode: 0600}, {Name: "C:/outside", Mode: 0600}, {Name: "safe:stream", Mode: 0600}, {Name: ".git/config", Mode: 0600}, {Name: "link", Typeflag: tar.TypeSymlink, Linkname: "/etc/passwd"}, {Name: "link", Typeflag: tar.TypeLink, Linkname: "outside"}} {
		var buffer bytes.Buffer
		w := tar.NewWriter(&buffer)
		if e := w.WriteHeader(header); e != nil {
			t.Fatal(e)
		}
		w.Close()
		if e := extractCodexTree(&buffer, t.TempDir()); e == nil {
			t.Fatal("unsafe archive accepted", header.Name)
		}
	}
}
func TestCodexArtifactsRejectOversizeAndDirectoryLinks(t *testing.T) {
	directory := t.TempDir()
	outside := t.TempDir()
	_ = os.WriteFile(filepath.Join(outside, "secret.txt"), []byte("private"), 0600)
	if e := makeTestLink(outside, filepath.Join(directory, "escape")); e != nil {
		t.Fatal(e)
	}
	if _, e := collectCodexArtifacts(directory); e == nil {
		t.Fatal("directory link artifact accepted")
	}
	if _, e := artifactBytes(directory, "escape/secret.txt"); e == nil {
		t.Fatal("linked artifact read")
	}
	directory = t.TempDir()
	file, e := os.Create(filepath.Join(directory, "large.bin"))
	if e != nil {
		t.Fatal(e)
	}
	_ = file.Truncate(codexArtifactLimit + 1)
	file.Close()
	if _, e := collectCodexArtifacts(directory); e == nil {
		t.Fatal("oversized artifact accepted")
	}
}
func TestCodexResourceWireFormatsAndLargeEscapedText(t *testing.T) {
	cfg := codexFixtureConfig(t)
	m, err := newCodexManager(cfg)
	if err != nil {
		t.Fatal(err)
	}
	id := randomID()
	directory := m.path(id, "artifacts")
	if err := os.MkdirAll(directory, 0700); err != nil {
		t.Fatal(err)
	}
	for name, data := range map[string][]byte{"empty.txt": {}, "ascii.pdf": []byte("%PDF-1.7\nASCII fixture"), "large.txt": bytes.Repeat([]byte("\x01"), (1<<20)+1)} {
		if err := os.WriteFile(filepath.Join(directory, name), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	artifacts, err := collectCodexArtifacts(directory)
	if err != nil {
		t.Fatal(err)
	}
	j := &codexJob{ID: id, Artifacts: artifacts}
	for _, item := range artifacts {
		result, err := m.artifact(j, item.ID)
		if err != nil {
			t.Fatal(err)
		}
		wire, err := json.Marshal(result)
		if err != nil || len(wire) > 12<<20 {
			t.Fatal("resource exceeds transport limit", err)
		}
		var decoded map[string]any
		if err := json.Unmarshal(wire, &decoded); err != nil {
			t.Fatal(err)
		}
		resource := decoded["content"].([]any)[1].(map[string]any)["resource"].(map[string]any)
		if item.Name == "empty.txt" {
			if text, ok := resource["text"]; !ok || text != "" {
				t.Fatal("empty text resource omitted required text field")
			}
		} else {
			blob, ok := resource["blob"].(string)
			if !ok || blob == "" {
				t.Fatal("binary/large resource missing blob")
			}
			if item.Name == "ascii.pdf" && resource["mimeType"] != "application/pdf" {
				t.Fatal("ASCII PDF misclassified")
			}
		}
	}
}

func TestCodexCleanupFailureBlocksNewJobsUntilConfirmedAbsent(t *testing.T) {
	s, count := codexFixtureService(t)
	original := s.codex.run
	var unavailable atomic.Bool
	unavailable.Store(true)
	s.codex.run = func(ctx context.Context, dir, program string, argv []string, input string) ([]byte, error) {
		if len(argv) > 0 && argv[0] == "container" && unavailable.Load() {
			return nil, fmt.Errorf("fixture daemon unavailable")
		}
		return original(ctx, dir, program, argv, input)
	}
	id := randomID()
	decodeResult(t, invoke(s, "codex_start", codexStartArgs(id, "review")))
	if state := waitCodex(t, s, id); state["state"] != "cleanup_required" {
		t.Fatal(state)
	}
	if !invoke(s, "codex_start", codexStartArgs(randomID(), "review")).IsError || count.Load() != 1 {
		t.Fatal("work started with unconfirmed cleanup")
	}
	if !invoke(s, "codex_cancel", args("job_id", id)).IsError {
		t.Fatal("unconfirmed cleanup reported as stopped")
	}
	unavailable.Store(false)
	state := decodeResult(t, invoke(s, "codex_cancel", args("job_id", id)))
	if state["state"] != "cancelled" {
		t.Fatal(state)
	}
}

func TestCodexGitUsesActualExactCommitInApprovedRef(t *testing.T) {
	git, e := exec.LookPath("git")
	if e != nil {
		t.Skip("Git unavailable")
	}
	source := t.TempDir()
	command := func(dir string, argv ...string) string {
		t.Helper()
		c := exec.Command(git, argv...)
		c.Dir = dir
		c.Env = append(os.Environ(), "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_GLOBAL="+os.DevNull)
		b, e := c.CombinedOutput()
		if e != nil {
			t.Fatalf("Git fixture: %v %s", e, b)
		}
		return strings.TrimSpace(string(b))
	}
	command(source, "init", "--initial-branch=fixture")
	_ = os.WriteFile(filepath.Join(source, "fixture.txt"), []byte("actual Git fixture"), 0600)
	command(source, "add", "fixture.txt")
	command(source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "core.hooksPath="+os.DevNull, "commit", "-m", "fixture")
	commit := command(source, "rev-parse", "HEAD")
	s, _ := codexFixtureService(t)
	original := s.codex.run
	s.codex.cfg.Git = git
	s.codex.run = func(ctx context.Context, dir, program string, argv []string, input string) ([]byte, error) {
		if program == git {
			if i := indexOf(argv, "fetch"); i >= 0 {
				argv = gitArgs("-c", "protocol.file.allow=always", "fetch", "--no-tags", "--depth=50", "--", source, "refs/heads/fixture")
			}
			output, err := runCodexCommand(ctx, dir, program, argv, input)
			if err != nil {
				t.Logf("fixture Git command failed: %v %v %s", argv, err, output)
			}
			return output, err
		}
		return original(ctx, dir, program, argv, input)
	}
	id := randomID()
	a := codexStartArgs(id, "review")
	a["commit"] = commit
	decodeResult(t, invoke(s, "codex_start", a))
	state := waitCodex(t, s, id)
	if state["state"] != "completed" {
		t.Fatal(state)
	}
	data, e := os.ReadFile(s.codex.path(id, "workspace", "fixture.txt"))
	if e != nil || string(data) != "actual Git fixture" {
		t.Fatal("wrong checkout", e)
	}
	id = randomID()
	a = codexStartArgs(id, "review")
	a["commit"] = strings.Repeat("f", 40)
	decodeResult(t, invoke(s, "codex_start", a))
	state = waitCodex(t, s, id)
	if state["state"] != "failed" {
		t.Fatal("commit outside ref accepted", state)
	}
}
func indexOf(values []string, value string) int {
	for i, v := range values {
		if v == value {
			return i
		}
	}
	return -1
}

func TestCodexNativeImageSurvivesMCPJSONRPC(t *testing.T) {
	s, _ := codexFixtureService(t)
	id := randomID()
	decodeResult(t, invoke(s, "codex_start", codexStartArgs(id, "review")))
	waitCodex(t, s, id)
	artifactID := hash([]byte("screen.png"))[:32]
	responses := mcpResponses(t, serviceCaller(s), toolRequest("codex_artifact_get", args("job_id", id, "artifact_id", artifactID)))
	content := responses[0]["result"].(map[string]any)["content"].([]any)
	if len(content) != 2 || content[1].(map[string]any)["type"] != "image" {
		t.Fatal("image lost at MCP boundary", fmt.Sprint(responses))
	}
}
