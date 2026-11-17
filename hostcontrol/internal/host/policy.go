package host

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"time"
)

const policyVersion = 1

type WorkspacePolicy struct {
	Name   string `json:"name"`
	Access string `json:"access"`
}

type remotePolicy struct {
	PolicyVersion int               `json:"policy_version"`
	Capabilities  []string          `json:"capabilities"`
	Workspaces    []WorkspacePolicy `json:"workspaces"`
}

type remoteStatus struct {
	remotePolicy
	Version   string    `json:"version"`
	Active    bool      `json:"active"`
	ExpiresAt time.Time `json:"expires_at"`
	Revoked   bool      `json:"revoked"`
}

func validAccess(access string) bool { return access == "read-only" || access == "read-write" }

func (c Config) access(name string) string {
	if _, ok := c.Roots[name]; !ok {
		return ""
	}
	if access, ok := c.WorkspaceAccess[name]; ok {
		return access
	}
	return "read-write" // Preserve previously explicit file read/write grants.
}

func (c Config) workspacePolicies() []WorkspacePolicy {
	workspaces := make([]WorkspacePolicy, 0, len(c.Roots))
	for name := range c.Roots {
		workspaces = append(workspaces, WorkspacePolicy{Name: name, Access: c.access(name)})
	}
	sort.Slice(workspaces, func(i, j int) bool { return workspaces[i].Name < workspaces[j].Name })
	return workspaces
}

// Discover only from the authenticated Windows service. Old or unavailable peers
// fail closed; the local adapter never substitutes its complete tool catalog.
func discoverPolicy(call Caller) (remotePolicy, Result, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	result, err := call(ctx, Call{OperationID: randomID(), Name: "host_status"})
	var policy remotePolicy
	if err != nil || result.IsError || len(result.Content) != 1 || result.Content[0].Type != "text" {
		return policy, Result{}, fmt.Errorf("host policy unavailable; access denied")
	}
	var status remoteStatus
	if json.Unmarshal([]byte(result.Content[0].Text), &status) != nil {
		return policy, Result{}, fmt.Errorf("host policy invalid; access denied")
	}
	policy = status.remotePolicy
	if policy.PolicyVersion != policyVersion || len(policy.Capabilities) == 0 || len(policy.Workspaces) == 0 {
		return policy, Result{}, fmt.Errorf("host policy invalid or agent upgrade required; access denied")
	}
	seen := map[string]bool{}
	for _, cap := range policy.Capabilities {
		if seen[cap] || cap != "files" && cap != "exec" && cap != "desktop" && cap != "codex" {
			return policy, Result{}, fmt.Errorf("host policy invalid; access denied")
		}
		seen[cap] = true
	}
	seen = map[string]bool{}
	for _, workspace := range policy.Workspaces {
		if workspace.Name == "" || seen[workspace.Name] || !validAccess(workspace.Access) {
			return policy, Result{}, fmt.Errorf("host policy invalid; access denied")
		}
		seen[workspace.Name] = true
	}
	// Forward a fixed status contract, not arbitrary future host metadata.
	return policy, textResult(status), nil
}

func (p remotePolicy) permitsTool(name string) bool {
	capability := capability(name)
	if capability == "" {
		return name == "host_status" || name == "session_info"
	}
	for _, allowed := range p.Capabilities {
		if capability == allowed {
			if name == "fs_write" || name == "fs_move" {
				for _, workspace := range p.Workspaces {
					if workspace.Access == "read-write" {
						return true
					}
				}
				return false
			}
			return true
		}
	}
	return false
}

func (p remotePolicy) tools() []Tool {
	tools := []Tool{}
	for _, tool := range Tools() {
		if p.permitsTool(tool.Name) {
			if capability(tool.Name) == "files" {
				names := []string{}
				for _, workspace := range p.Workspaces {
					if p.permitsWorkspace(workspace.Name, tool.Name == "fs_write" || tool.Name == "fs_move") {
						names = append(names, workspace.Name)
					}
				}
				tool.InputSchema["properties"].(map[string]any)["workspace"].(map[string]any)["enum"] = names
			}
			tools = append(tools, tool)
		}
	}
	return tools
}

func (p remotePolicy) permitsWorkspace(name string, write bool) bool {
	for _, workspace := range p.Workspaces {
		if workspace.Name == name {
			return workspace.Access == "read-write" || !write && workspace.Access == "read-only"
		}
	}
	return false
}
