package host

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func mcpResponses(t *testing.T, call Caller, requests ...map[string]any) []map[string]any {
	t.Helper()
	var input, output bytes.Buffer
	encoder := json.NewEncoder(&input)
	_ = encoder.Encode(map[string]any{"jsonrpc": "2.0", "id": 1, "method": "initialize"})
	for n, request := range requests {
		request["jsonrpc"], request["id"] = "2.0", n+2
		_ = encoder.Encode(request)
	}
	if err := RunMCP(&input, &output, call); err != nil {
		t.Fatal(err)
	}
	responses := []map[string]any{}
	decoder := json.NewDecoder(&output)
	for {
		var response map[string]any
		if err := decoder.Decode(&response); err == io.EOF {
			break
		} else if err != nil {
			t.Fatal(err)
		}
		responses = append(responses, response)
	}
	return responses[1:]
}

func serviceCaller(s *Service) Caller {
	return func(_ context.Context, c Call) (Result, error) { return s.Call(c), nil }
}

func toolRequest(name string, arguments map[string]any) map[string]any {
	return map[string]any{"method": "tools/call", "params": map[string]any{"name": name, "arguments": arguments}}
}

func TestMCPUsesLivePolicyWhilePausedAndRejectsBypass(t *testing.T) {
	s := testService(t)
	s.cfg.Capabilities = []string{"files"}
	s.cfg.WorkspaceAccess = map[string]string{"work": "read-only"}
	forwarded := []string{}
	caller := func(ctx context.Context, c Call) (Result, error) {
		forwarded = append(forwarded, c.Name)
		return s.Call(c), nil
	}
	responses := mcpResponses(t, caller,
		map[string]any{"method": "tools/list"},
		toolRequest("exec_start", args("workspace", "work", "program", "unused")),
		toolRequest("fs_write", args("workspace", "work", "path", "blocked.txt", "text", "blocked")),
		toolRequest("fs_read", args("workspace", "work", "path", "blocked.txt")),
	)
	listed := responses[0]["result"].(map[string]any)["tools"].([]any)
	names := []string{}
	for _, tool := range listed {
		names = append(names, tool.(map[string]any)["name"].(string))
	}
	if strings.Join(names, ",") != "host_status,session_info,fs_list,fs_read" {
		t.Fatal("paused readonly policy exposed unexpected tools", names)
	}
	for _, response := range responses[1:] {
		if response["result"].(map[string]any)["isError"] != true {
			t.Fatal("forbidden operation accepted", response)
		}
	}
	if strings.Join(forwarded, ",") != "host_status,host_status,host_status,host_status,fs_read" {
		t.Fatal("disabled tool forwarded to host", forwarded)
	}
	if _, err := os.Stat(filepath.Join(s.cfg.Roots["work"], "blocked.txt")); !os.IsNotExist(err) {
		t.Fatal("disabled write had side effects")
	}
}

func TestMCPPolicyFailureNeverFallsBackToFullCatalog(t *testing.T) {
	for _, mode := range []string{"offline", "old-agent", "unknown-capability", "invalid-mode"} {
		t.Run(mode, func(t *testing.T) {
			nonStatus := 0
			caller := func(_ context.Context, c Call) (Result, error) {
				if c.Name != "host_status" {
					nonStatus++
				}
				if mode == "offline" {
					return Result{}, fmt.Errorf("private transport details must not escape")
				}
				policy := remotePolicy{policyVersion, []string{"files"}, []WorkspacePolicy{{"work", "read-write"}}}
				if mode == "old-agent" {
					policy.PolicyVersion = 0
				}
				if mode == "unknown-capability" {
					policy.Capabilities = []string{"anything"}
				}
				if mode == "invalid-mode" {
					policy.Workspaces[0].Access = "anything"
				}
				return textResult(policy), nil
			}
			responses := mcpResponses(t, caller, map[string]any{"method": "tools/list"}, toolRequest("fs_read", args("workspace", "work", "path", "a.txt")))
			if responses[0]["error"] == nil || responses[1]["result"].(map[string]any)["isError"] != true || nonStatus != 0 {
				t.Fatal("failed discovery exposed tools or forwarded a call", responses, nonStatus)
			}
			data, _ := json.Marshal(responses)
			if strings.Contains(string(data), "private transport") {
				t.Fatal("transport internals exposed")
			}
		})
	}
}

func TestMixedWorkspacesAndPolicyRefresh(t *testing.T) {
	s := testService(t)
	s.cfg.Capabilities = []string{"files"}
	s.cfg.Roots["readonly"] = t.TempDir()
	s.cfg.WorkspaceAccess = map[string]string{"readonly": "read-only"}
	if err := s.Enable(1); err != nil {
		t.Fatal(err)
	}
	responses := mcpResponses(t, serviceCaller(s), toolRequest("fs_write", args("workspace", "readonly", "path", "a.txt", "text", "bad")), toolRequest("fs_write", args("workspace", "work", "path", "a.txt", "text", "ok")))
	if responses[0]["result"].(map[string]any)["isError"] != true || responses[1]["result"].(map[string]any)["isError"] == true {
		t.Fatal("per-workspace grant ignored", responses)
	}
	// Discovery must not cache an earlier read/write grant across requests.
	s.cfg.WorkspaceAccess["work"] = "read-only"
	responses = mcpResponses(t, serviceCaller(s), toolRequest("fs_write", args("workspace", "work", "path", "new.txt", "text", "bad")))
	if responses[0]["result"].(map[string]any)["isError"] != true {
		t.Fatal("adapter reused stale grant")
	}
}

func TestReadonlyHostRejectsDirectCallsAndCachedWrites(t *testing.T) {
	s := testService(t)
	s.cfg.Capabilities = []string{"files"}
	if err := s.Enable(1); err != nil {
		t.Fatal(err)
	}
	write := Call{randomID(), "fs_write", args("workspace", "work", "path", "kept.txt", "text", "original")}
	if s.Call(write).IsError {
		t.Fatal("initial write failed")
	}
	s.cfg.WorkspaceAccess = map[string]string{"work": "read-only"}
	for _, call := range []Call{write, {randomID(), "fs_write", args("workspace", "work", "path", "new.txt", "text", "bad")}, {randomID(), "fs_move", args("workspace", "work", "path", "kept.txt", "to", "moved.txt")}} {
		if !s.Call(call).IsError {
			t.Fatal("readonly write or mutation replay accepted")
		}
	}
	read := decodeResult(t, invoke(s, "fs_read", args("workspace", "work", "path", "kept.txt")))
	if read["text"] != "original" {
		t.Fatal("readonly denial changed original")
	}
	for _, path := range []string{"new.txt", "moved.txt"} {
		if _, err := os.Stat(filepath.Join(s.cfg.Roots["work"], path)); !os.IsNotExist(err) {
			t.Fatal("readonly operation left side effects")
		}
	}
}

func persistedTestService(t *testing.T) (*Service, string) {
	t.Helper()
	c := testService(t).cfg
	c.Listen, c.PeerIP = "100.80.0.1:7443", "100.80.0.2"
	c.CA, c.Cert = "ca", "cert"
	c.Capabilities = []string{"files"}
	data, _ := json.Marshal(c)
	path := filepath.Join(t.TempDir(), "agent.json")
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	s, err := NewServiceFromFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return s, path
}

func TestLocalPermissionPersistencePauseAndExternalEditGuard(t *testing.T) {
	s, path := persistedTestService(t)
	if err := s.Enable(1); err != nil {
		t.Fatal(err)
	}
	if err := s.setWorkspaceAccess("work", "read-only"); err != nil {
		t.Fatal(err)
	}
	if s.active() {
		t.Fatal("permission change retained active authorization")
	}
	restarted, err := NewServiceFromFile(path)
	if err != nil || restarted.cfg.access("work") != "read-only" {
		t.Fatal("mode did not survive restart", err)
	}
	// Only the localhost panel receives actual Windows paths.
	remote, _ := json.Marshal(s.Status())
	local, _ := json.Marshal(s.localStatus())
	if bytes.Contains(remote, []byte("local_workspaces")) || bytes.Contains(remote, []byte("path")) || !bytes.Contains(local, []byte("local_workspaces")) {
		t.Fatal("local/private and remote policy not separated")
	}
	before, _ := os.ReadFile(path)
	if err := s.setWorkspaceAccess("outside", "read-write"); err == nil {
		t.Fatal("panel added an unapproved root")
	}
	if err := s.setWorkspaceAccess("work", "anything"); err == nil {
		t.Fatal("panel accepted invalid mode")
	}
	after, _ := os.ReadFile(path)
	if !bytes.Equal(before, after) {
		t.Fatal("invalid mode request changed config")
	}
	external := append(before, '\n')
	if err := os.WriteFile(path, external, 0600); err != nil {
		t.Fatal(err)
	}
	if err := s.setWorkspaceAccess("work", "read-write"); err == nil {
		t.Fatal("concurrent external configuration overwritten")
	}
	after, _ = os.ReadFile(path)
	if !bytes.Equal(external, after) {
		t.Fatal("external change lost")
	}
}

func TestLocalPermissionEndpointRejectsRemoteAndUnauthenticatedChanges(t *testing.T) {
	s, _ := persistedTestService(t)
	address, err := s.StartControl()
	if err != nil {
		t.Fatal(err)
	}
	parsed, _ := url.Parse(address)
	token := parsed.Fragment
	parsed.Fragment = ""
	base := strings.TrimSuffix(parsed.String(), "/")
	path := "/workspace-access?workspace=work&access=read-only"
	request := func(auth, origin string) int {
		r, _ := http.NewRequest("POST", base+path, nil)
		r.Header.Set("Authorization", "Bearer "+auth)
		r.Header.Set("Origin", origin)
		response, err := http.DefaultClient.Do(r)
		if err != nil {
			t.Fatal(err)
		}
		defer response.Body.Close()
		return response.StatusCode
	}
	if request("", base) != 403 || request(token, "https://evil.invalid") != 403 || s.cfg.access("work") != "read-write" {
		t.Fatal("unauthenticated permission change")
	}
	if !invoke(s, "workspace_access", args("workspace", "work", "access", "read-only")).IsError {
		t.Fatal("remote tool changed permissions")
	}
	if request(token, base) != 200 || s.cfg.access("work") != "read-only" {
		t.Fatal("authenticated local permission change failed")
	}
}

func TestWorkspaceAccessValidation(t *testing.T) {
	s, _ := persistedTestService(t)
	for _, modes := range []map[string]string{{"outside": "read-only"}, {"work": "all"}} {
		c := s.cfg
		c.WorkspaceAccess = modes
		if c.Validate() == nil {
			t.Fatal("invalid workspace access accepted")
		}
	}
}

func TestMCPStatusForwardsOnlyDocumentedFields(t *testing.T) {
	caller := func(_ context.Context, _ Call) (Result, error) {
		return textResult(map[string]any{
			"policy_version": policyVersion, "capabilities": []string{"files"},
			"workspaces":       []WorkspacePolicy{{"work", "read-write"}},
			"local_workspaces": []map[string]string{{"path": "private-host-path"}},
			"hostname":         "private-hostname", "network": "private-network-info",
		}), nil
	}
	responses := mcpResponses(t, caller, toolRequest("host_status", nil))
	data, _ := json.Marshal(responses)
	if bytes.Contains(data, []byte("private-")) || bytes.Contains(data, []byte("local_workspaces")) {
		t.Fatal("adapter forwarded host metadata outside the status contract")
	}
}
