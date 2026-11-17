package host

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"image"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// This opt-in acceptance test uses a real dedicated Codex login, Git, Docker and
// model calls. It must never point at a production agent config or job directory.
// The temporary transport is loopback-only with fresh mutually pinned TLS keys.
func TestCodexLiveIntegration(t *testing.T) {
	configPath := os.Getenv("HOSTCONTROL_CODEX_LIVE_CONFIG")
	if configPath == "" {
		t.Skip("set HOSTCONTROL_CODEX_LIVE_CONFIG to an isolated QA config to run real model/container acceptance")
	}
	var live struct {
		Agent    Config `json:"agent"`
		Commit   string `json:"commit"`
		Evidence string `json:"evidence"`
	}
	if err := ReadJSON(configPath, &live); err != nil {
		t.Fatal("cannot load private QA config:", err)
	}
	if err := live.Agent.Validate(); err != nil {
		t.Fatal(err)
	}
	if live.Agent.Codex == nil || len(live.Agent.Capabilities) != 2 || !live.Agent.Has("files") || !live.Agent.Has("codex") || !commitPattern.MatchString(live.Commit) || !plainDirectory(live.Evidence) {
		t.Fatal("use a dedicated files+codex QA config with an approved exact commit/evidence directory")
	}
	if live.Agent.Codex.MaxRounds != 2 || len(live.Agent.Codex.Projects) != 1 {
		t.Fatal("live QA requires one dedicated project and exactly two permitted rounds")
	}
	ca, sc, sk, sf, cc, ck, cf := testTLSFiles(t)
	live.Agent.Key, live.Agent.CA, live.Agent.Cert = sk, ca, sc
	live.Agent.PeerIP, live.Agent.PeerFingerprint = "127.0.0.1", cf
	s, err := NewService(live.Agent)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = s.Pause(false)
		s.codex.mu.Lock()
		var jobs []*codexJob
		for _, j := range s.codex.jobs {
			jobs = append(jobs, j)
		}
		s.codex.mu.Unlock()
		for _, j := range jobs {
			select {
			case <-j.done:
			case <-time.After(30 * time.Second):
				t.Error("QA cleanup unconfirmed for", j.ID)
			}
		}
	})
	tlsConfig, err := TLSConfig(ca, sc, sk, cf, true)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewUnstartedServer(s.Handler())
	server.TLS = tlsConfig
	server.StartTLS()
	defer server.Close()
	caller, err := NewCaller(ClientConfig{server.URL, ca, cc, ck, sf})
	if err != nil {
		t.Fatal(err)
	}
	call := func(name string, a map[string]any) Result {
		t.Helper()
		ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
		defer cancel()
		result, err := caller(ctx, Call{randomID(), name, a})
		if err != nil {
			t.Fatal("real mTLS call failed:", name, err)
		}
		return result
	}
	policy, _, err := discoverPolicy(caller)
	if err != nil || !policy.permitsTool("codex_start") || policy.permitsTool("exec_start") || policy.permitsTool("desktop_capture") {
		t.Fatal("unexpected QA policy", err)
	}
	if !call("codex_projects", nil).IsError {
		t.Fatal("paused session admitted Codex access")
	}
	if err := s.Enable(15); err != nil {
		t.Fatal(err)
	}
	projects := decodeResult(t, call("codex_projects", nil))["projects"].([]any)
	if len(projects) != 1 {
		t.Fatal("live QA must approve exactly one synthetic/public project")
	}
	project := projects[0].(map[string]any)
	alias := project["project"].(string)
	ref := project["refs"].([]any)[0].(string)
	nonce := "HOSTCONTROL-LIVE-" + randomID()[:12]
	start := func(mode, suite, instruction string) string {
		t.Helper()
		id := randomID()
		a := args("request_id", id, "project", alias, "ref", ref, "commit", live.Commit, "mode", mode, "instruction", instruction)
		if suite != "" {
			a["suite"] = suite
		}
		decodeResult(t, call("codex_start", a))
		// A fresh transport operation reusing the same request must not start again.
		decodeResult(t, call("codex_start", a))
		return id
	}
	wait := func(id string) map[string]any {
		t.Helper()
		deadline := time.Now().Add(time.Duration(live.Agent.Codex.TimeoutSeconds+40) * time.Second)
		for time.Now().Before(deadline) {
			state := decodeResult(t, call("codex_status", args("job_id", id)))
			if finishedCodexState(state["state"].(string)) || state["state"] == "cleanup_required" {
				t.Log("real job finished", id, state["state"])
				return state
			}
			time.Sleep(time.Second)
		}
		t.Fatal("live task did not reach confirmed terminal state", id)
		return nil
	}
	artifact := func(id, name string) Result {
		t.Helper()
		result := decodeResult(t, call("codex_result", args("job_id", id)))
		for _, value := range result["artifacts"].([]any) {
			item := value.(map[string]any)
			if item["name"] == name {
				// Exercise actual MCP JSON-RPC serialization, plus real HTTPS/mTLS.
				responses := mcpResponses(t, caller, toolRequest("codex_artifact_get", args("job_id", id, "artifact_id", item["artifact_id"].(string))))
				wire, _ := json.Marshal(responses[0]["result"])
				var output Result
				if err := json.Unmarshal(wire, &output); err != nil || output.IsError {
					t.Fatal("MCP artifact failed", err)
				}
				return output
			}
		}
		t.Fatal("expected real artifact not registered:", name)
		return Result{}
	}
	getText := func(result Result) string {
		t.Helper()
		if len(result.Content) != 2 || result.Content[1].Resource == nil || result.Content[1].Resource.Text == nil {
			t.Fatal("missing embedded text")
		}
		return *result.Content[1].Resource.Text
	}
	// The failing suite is configured locally. Codex must preserve its real exit
	// code while producing a genuine headless-browser screenshot of a synthetic page.
	instruction := fmt.Sprintf("Integration acceptance in the disposable container. The coordinator ran an intentionally failing test suite: report its actual exit code without rerunning it. Read README from the exact snapshot. Write /artifacts/provenance.txt containing marker %s and the source commit. Create /artifacts/probe.html displaying that marker in a large heading on a pale blue background, then run Chromium to capture the ACTUAL rendered page at 640x480 to /artifacts/probe.png (chromium --headless --no-sandbox --disable-dev-shm-usage --screenshot=/artifacts/probe.png --window-size=640,480 file:///artifacts/probe.html). The browser is contained by the outer Docker sandbox. Do not fabricate image bytes, access credentials, install dependencies, edit source, publish or deploy. Briefly explain the intentional test failure.", nonce)
	testID := start("test", "expected-failure", instruction)
	if state := wait(testID); state["state"] != "completed" {
		t.Fatal("real test/model job failed; inspect private job", state)
	}
	result := decodeResult(t, call("codex_result", args("job_id", testID)))
	report := result["report"].(map[string]any)
	tests := report["tests"].(map[string]any)
	if tests["exit_code"] != float64(7) || tests["passed"] != false || report["agent_completed"] != true || result["commit"] != live.Commit {
		t.Fatal("actual test failure/commit not preserved")
	}
	if !strings.Contains(getText(artifact(testID, "provenance.txt")), nonce) {
		t.Fatal("model did not return current challenge marker")
	}
	imageResult := artifact(testID, "probe.png")
	if len(imageResult.Content) != 2 || imageResult.Content[1].Type != "image" {
		t.Fatal("screenshot did not arrive as native MCP image")
	}
	pngBytes, err := base64.StdEncoding.DecodeString(imageResult.Content[1].Data)
	if err != nil {
		t.Fatal(err)
	}
	imageConfig, _, err := image.DecodeConfig(bytes.NewReader(pngBytes))
	if err != nil || imageConfig.Width != 640 || imageConfig.Height != 480 {
		t.Fatal("wrong actual browser screenshot", imageConfig, err)
	}
	if err := os.WriteFile(filepath.Join(live.Evidence, "probe.png"), pngBytes, 0600); err != nil {
		t.Fatal(err)
	}

	implementID := start("implement", "", "Create one new file HOSTCONTROL_LIVE_PROBE.md containing marker "+nonce+" and a short summary of README. Do not run tests, modify other files, commit, push or deploy. This is a disposable integration acceptance task.")
	if state := wait(implementID); state["state"] != "completed" {
		t.Fatal(state)
	}
	patch := getText(artifact(implementID, "changes.patch"))
	if !strings.Contains(patch, nonce) || !strings.Contains(patch, "new file mode") {
		t.Fatal("real implementation patch missing new file/marker")
	}
	childID := randomID()
	decodeResult(t, call("codex_continue", args("request_id", childID, "job_id", implementID, "instruction", "Append the exact line 'Continuation round verified' to HOSTCONTROL_LIVE_PROBE.md, preserving its existing contents. Do not test, commit, push or deploy.")))
	if state := wait(childID); state["state"] != "completed" || state["round"] != float64(2) {
		t.Fatal(state)
	}
	childPatch := getText(artifact(childID, "changes.patch"))
	if !strings.Contains(childPatch, nonce) || !strings.Contains(childPatch, "Continuation round verified") {
		t.Fatal("continuation lost prior implementation")
	}
	if !call("codex_continue", args("request_id", randomID(), "job_id", childID, "instruction", "must be rejected beyond approved rounds")).IsError {
		t.Fatal("round limit not enforced")
	}

	// This suite sleeps before any model invocation. Cancel a real running
	// container and independently verify Docker reports it absent.
	cancelID := start("test", "slow", "Do not start model analysis until the fixed suite finishes.")
	containerName := "hostcontrol-codex-" + cancelID
	ready := false
	for deadline := time.Now().Add(45 * time.Second); time.Now().Before(deadline); {
		out, err := runCodexCommand(context.Background(), live.Agent.Codex.Directory, live.Agent.Codex.Docker, []string{"inspect", "--format", "{{.State.Running}}", containerName}, "")
		if err == nil && strings.TrimSpace(string(out)) == "true" {
			ready = true
			break
		}
		time.Sleep(time.Second)
	}
	if !ready {
		t.Fatal("real cancellation container never started")
	}
	inspectContext, inspectCancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer inspectCancel()
	inspectBytes, err := runCodexCommand(inspectContext, live.Agent.Codex.Directory, live.Agent.Codex.Docker, []string{"inspect", containerName}, "")
	if err != nil {
		t.Fatal("cannot inspect real container boundary", err)
	}
	var inspected []struct {
		HostConfig struct {
			ReadonlyRootfs bool
			Privileged     bool
			CapDrop        []string
			SecurityOpt    []string
			Memory         int64
			NetworkMode    string
		}
		Mounts []struct {
			Type, Destination string
			RW                bool
		}
	}
	if err := json.Unmarshal(inspectBytes, &inspected); err != nil || len(inspected) != 1 {
		t.Fatal("invalid real Docker inspection", err)
	}
	boundary := inspected[0]
	if !boundary.HostConfig.ReadonlyRootfs || boundary.HostConfig.Privileged || !contains(boundary.HostConfig.CapDrop, "ALL") || !contains(boundary.HostConfig.SecurityOpt, "no-new-privileges") || boundary.HostConfig.Memory != 2<<30 || boundary.HostConfig.NetworkMode != "bridge" {
		t.Fatal("real container boundary differs from approved configuration")
	}
	approvedMounts := map[string]bool{"/workspace": false, "/baseline": false, "/input": false, "/artifacts": true, "/auth/auth.json": false}
	for _, mount := range boundary.Mounts {
		if mount.Type != "bind" {
			continue
		}
		writable, ok := approvedMounts[mount.Destination]
		if !ok || writable != mount.RW {
			t.Fatal("unexpected real bind mount")
		}
		delete(approvedMounts, mount.Destination)
	}
	if len(approvedMounts) != 0 {
		t.Fatal("missing real bind mounts")
	}
	probe := `const fs=require('node:fs'),cp=require('node:child_process');for(const path of ['/workspace/hostcontrol-write-probe','/baseline/hostcontrol-write-probe','/hostcontrol-write-probe']){let blocked=false;try{fs.writeFileSync(path,'synthetic QA probe')}catch(e){if(e.code!=='EROFS')throw e;blocked=true}if(!blocked)throw Error('expected read-only mount')}fs.writeFileSync('/tmp/hostcontrol-exec-probe','#!/bin/sh\nexit 0\n',{mode:0o700});cp.execFileSync('/tmp/hostcontrol-exec-probe');console.log('read-only mounts and executable temporary build scratch verified');`
	if _, err := runCodexCommand(inspectContext, live.Agent.Codex.Directory, live.Agent.Codex.Docker, []string{"exec", containerName, "node", "-e", probe}, ""); err != nil {
		t.Fatal("real write/execute boundary probe failed", err)
	}
	decodeResult(t, call("codex_cancel", args("job_id", cancelID)))
	if state := wait(cancelID); state["state"] != "cancelled" {
		t.Fatal("cancellation unconfirmed", state)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	out, err := runCodexCommand(ctx, live.Agent.Codex.Directory, live.Agent.Codex.Docker, []string{"container", "ls", "--all", "--filter", "name=^/" + containerName + "$", "--format", "{{.Names}}"}, "")
	if err != nil || strings.TrimSpace(string(out)) != "" {
		t.Fatal("cancelled container still present", err)
	}
	if err := s.Pause(false); err != nil {
		t.Fatal(err)
	}
	if !call("codex_result", args("job_id", testID)).IsError {
		t.Fatal("paused session leaked artifacts")
	}
	evidence := map[string]any{"observed_at": time.Now().UTC(), "transport": "real loopback mTLS and MCP JSON-RPC", "image": live.Agent.Codex.Image, "source_commit": live.Commit, "test_job": testID, "implement_job": implementID, "continue_job": childID, "cancel_job": cancelID, "fixed_test_exit_code": tests["exit_code"], "native_mcp_image": true, "browser_screenshot": "probe.png", "docker_boundary_inspected": true, "readonly_write_and_tmp_exec_verified": true, "production_changed": false, "claude_ui_display_verified": false}
	evidenceBytes, _ := json.MarshalIndent(evidence, "", "  ")
	if err := os.WriteFile(filepath.Join(live.Evidence, "acceptance.json"), evidenceBytes, 0600); err != nil {
		t.Fatal(err)
	}
	t.Log("REAL ACCEPTANCE PASSED: exact Git commit, failed-test evidence, model execution, browser PNG over MCP, implementation/continuation and confirmed Docker cancellation")
}
