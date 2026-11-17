package host

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// NewServiceFromFile enables local-only, persistent changes to workspace modes.
// Roots, capabilities, certificates and network endpoints cannot be edited by
// the control panel or the remote MCP peer.
func NewServiceFromFile(path string) (*Service, error) {
	path, err := filepath.Abs(path)
	if err != nil {
		return nil, fmt.Errorf("invalid configuration path")
	}
	data, err := os.ReadFile(path)
	if err != nil || len(data) > 1<<20 {
		return nil, fmt.Errorf("configuration unavailable")
	}
	var config Config
	if err := decodeJSON(bytes.NewReader(data), &config); err != nil {
		return nil, err
	}
	if err := config.Validate(); err != nil {
		return nil, err
	}
	s, err := NewService(config)
	if err != nil {
		return nil, err
	}
	s.configPath, s.configHash = path, sha256.Sum256(data)
	return s, nil
}

// Only StartControl calls this mutator. Serialize with all tool operations so a
// completed downgrade cannot leave a write in flight or replay an old result.
func (s *Service) setWorkspaceAccess(name, access string) error {
	s.callMu.Lock()
	defer s.callMu.Unlock()
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.cfg.Roots[name]; !ok || !validAccess(access) {
		return fmt.Errorf("unknown workspace or invalid access mode")
	}
	if s.configPath == "" {
		return fmt.Errorf("persistent configuration unavailable; restart the agent with its configuration file")
	}
	if s.revoked {
		return fmt.Errorf("pairing revoked")
	}
	info, err := os.Lstat(s.configPath)
	if err != nil || !info.Mode().IsRegular() {
		return fmt.Errorf("configuration must be a regular file")
	}
	before, err := os.ReadFile(s.configPath)
	if err != nil || sha256.Sum256(before) != s.configHash {
		return fmt.Errorf("configuration changed externally; restart before changing permissions")
	}
	next := s.cfg
	next.WorkspaceAccess = make(map[string]string, len(s.cfg.WorkspaceAccess)+1)
	for workspace, mode := range s.cfg.WorkspaceAccess {
		next.WorkspaceAccess[workspace] = mode
	}
	next.WorkspaceAccess[name] = access
	data, err := json.MarshalIndent(next, "", "  ")
	if err != nil {
		return fmt.Errorf("cannot encode configuration")
	}
	data = append(data, '\n')
	file, err := os.CreateTemp(filepath.Dir(s.configPath), ".agent-policy-*")
	if err != nil {
		return fmt.Errorf("cannot prepare configuration")
	}
	defer os.Remove(file.Name())
	_, err = file.Write(data)
	if err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err != nil || closeErr != nil {
		return fmt.Errorf("cannot save configuration")
	}
	// Retain an owner-private rollback copy alongside the private configuration.
	backup := s.configPath + ".before-policy-" + randomID()
	if err := writeNew(backup, before); err != nil {
		return fmt.Errorf("cannot back up configuration")
	}
	latest, err := os.ReadFile(s.configPath)
	if err != nil || sha256.Sum256(latest) != s.configHash {
		return fmt.Errorf("configuration changed externally; restart before changing permissions")
	}
	if err := os.Rename(file.Name(), s.configPath); err != nil {
		return fmt.Errorf("cannot replace configuration")
	}
	s.cfg.WorkspaceAccess, s.configHash = next.WorkspaceAccess, sha256.Sum256(data)
	s.deadline = time.Time{} // Applying a mode always requires a new local session.
	s.CancelTasks("workspace permissions changed")
	if s.codex != nil {
		s.codex.cancelAll("workspace permissions changed")
	}
	return nil
}

func (s *Service) localStatus() map[string]any {
	status := s.Status()
	s.mu.Lock()
	defer s.mu.Unlock()
	workspaces := []map[string]string{}
	for _, workspace := range s.cfg.workspacePolicies() {
		workspaces = append(workspaces, map[string]string{"name": workspace.Name, "path": s.cfg.Roots[workspace.Name], "access": workspace.Access})
	}
	status["local_workspaces"] = workspaces
	status["permissions_editable"] = s.configPath != "" && !s.revoked
	status["max_session_minutes"] = s.cfg.SessionMinutes
	return status
}
