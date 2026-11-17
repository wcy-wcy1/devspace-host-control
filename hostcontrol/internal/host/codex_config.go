package host

import (
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// This is a separate opt-in capability. Neither MCP nor the control page can
// choose executables, images, mounts, credentials, repositories or test commands.
type CodexConfig struct {
	Directory      string                  `json:"directory"`
	Git            string                  `json:"git"`
	Docker         string                  `json:"docker"`
	Image          string                  `json:"image"`
	AuthDirectory  string                  `json:"auth_directory"`
	TimeoutSeconds int                     `json:"timeout_seconds"`
	MaxRounds      int                     `json:"max_rounds"`
	Projects       map[string]CodexProject `json:"projects"`
}
type CodexProject struct {
	Repository string              `json:"repository"`
	Refs       []string            `json:"refs"`
	Modes      []string            `json:"modes"`
	Suites     map[string][]string `json:"suites,omitempty"`
}

var aliasPattern = regexp.MustCompile(`^[a-z][a-z0-9_-]{0,47}$`)
var commitPattern = regexp.MustCompile(`^(?:[a-f0-9]{40}|[a-f0-9]{64})$`)
var imagePattern = regexp.MustCompile(`^(?:[a-zA-Z0-9./:_-]+@)?sha256:[a-f0-9]{64}$`)

func contains(values []string, value string) bool {
	for _, v := range values {
		if v == value {
			return true
		}
	}
	return false
}
func pathsOverlap(a, b string) bool {
	for _, pair := range [][2]string{{a, b}, {b, a}} {
		r, e := filepath.Rel(pair[0], pair[1])
		if e == nil && (r == "." || filepath.IsLocal(r)) {
			return true
		}
	}
	return false
}
func plainDirectory(path string) bool {
	if !filepath.IsAbs(path) || strings.ContainsAny(path, ",\x00\r\n") {
		return false
	}
	clean, err := filepath.EvalSymlinks(path)
	if err != nil || !strings.EqualFold(filepath.Clean(path), filepath.Clean(clean)) {
		return false
	}
	st, err := os.Lstat(path)
	return err == nil && st.IsDir() && st.Mode()&os.ModeSymlink == 0
}
func validateCodexConfig(c *Config) error {
	if !c.Has("codex") {
		if c.Codex != nil {
			return fmt.Errorf("codex configuration requires explicit codex capability")
		}
		return nil
	}
	v := c.Codex
	if v == nil || !plainDirectory(v.Directory) || !plainDirectory(v.AuthDirectory) || pathsOverlap(v.Directory, v.AuthDirectory) {
		return fmt.Errorf("Codex requires separate existing private job and dedicated auth directories without links")
	}
	authInfo, authErr := os.Lstat(filepath.Join(v.AuthDirectory, "auth.json"))
	if authErr != nil || !authInfo.Mode().IsRegular() || authInfo.Size() > 128<<10 {
		return fmt.Errorf("Codex requires a dedicated regular auth.json file, at most 128 KiB")
	}
	for _, root := range c.Roots {
		if pathsOverlap(root, v.Directory) || pathsOverlap(root, v.AuthDirectory) {
			return fmt.Errorf("Codex jobs and credentials must be outside file workspaces")
		}
	}
	for _, program := range []string{v.Git, v.Docker} {
		st, e := os.Stat(program)
		if !filepath.IsAbs(program) || e != nil || !st.Mode().IsRegular() {
			return fmt.Errorf("Codex Git/Docker executables must be explicit existing absolute paths")
		}
	}
	if !imagePattern.MatchString(v.Image) || v.TimeoutSeconds < 30 || v.TimeoutSeconds > 1800 || v.MaxRounds < 1 || v.MaxRounds > 5 || len(v.Projects) == 0 || len(v.Projects) > 16 {
		return fmt.Errorf("Codex requires a digest-pinned Linux runner image, 30..1800 second timeout, 1..5 rounds and 1..16 projects")
	}
	for alias, p := range v.Projects {
		u, e := url.Parse(p.Repository)
		if !aliasPattern.MatchString(alias) || e != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path == "" {
			return fmt.Errorf("Codex projects require aliases and fixed credential-free HTTPS repository URLs")
		}
		if len(p.Refs) == 0 || len(p.Refs) > 16 || len(p.Modes) == 0 || len(p.Modes) > 3 {
			return fmt.Errorf("Codex projects require explicit refs and modes")
		}
		for _, ref := range p.Refs {
			if !strings.HasPrefix(ref, "refs/heads/") || strings.Contains(ref, "..") || strings.Contains(ref, "@{") || strings.ContainsAny(ref, " ~^:?*[\\\x00\r\n") || strings.HasSuffix(ref, "/") || strings.HasSuffix(ref, ".lock") {
				return fmt.Errorf("Codex requires exact branch refs, no patterns or revisions")
			}
		}
		for _, mode := range p.Modes {
			if !contains([]string{"test", "review", "implement"}, mode) {
				return fmt.Errorf("unknown Codex project mode")
			}
		}
		for name, argv := range p.Suites {
			if !aliasPattern.MatchString(name) || len(argv) == 0 || len(argv) > 32 || strings.HasPrefix(argv[0], "-") {
				return fmt.Errorf("invalid locally configured test suite")
			}
			for _, arg := range argv {
				if arg == "" || len(arg) > 1024 || strings.ContainsRune(arg, 0) {
					return fmt.Errorf("invalid test argument")
				}
			}
		}
		if contains(p.Modes, "test") && len(p.Suites) == 0 {
			return fmt.Errorf("test mode requires a fixed local test suite")
		}
	}
	return nil
}
