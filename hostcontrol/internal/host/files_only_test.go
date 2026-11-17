package host

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestFilesOnlyBlocksExecutionAndDesktop(t *testing.T) {
	s := testService(t)
	s.cfg.Capabilities = []string{"files"}
	verifyFilesOnly(t, s)
}

// Opt-in verification of the actual deployment config, without starting a
// listener or changing the live agent's authorization state. Only generated
// test fixtures inside the allowed folder and t.TempDir are accessed.
func TestDeployedFilesOnlyConfiguration(t *testing.T) {
	path := os.Getenv("HOSTCONTROL_FILES_ONLY_CONFIG")
	if path == "" {
		t.Skip("deployment configuration not selected")
	}
	var c Config
	if e := ReadJSON(path, &c); e != nil {
		t.Fatal(e)
	}
	if e := c.Validate(); e != nil {
		t.Fatal(e)
	}
	if len(c.Capabilities) != 1 || c.Capabilities[0] != "files" || len(c.Roots) != 1 {
		t.Fatal("deployment does not have exactly one files-only workspace")
	}
	if filepath.Clean(c.Roots["work"]) != filepath.Clean(os.Getenv("HOSTCONTROL_ALLOWED_DIRECTORY")) {
		t.Fatal("deployment root differs from the selected directory")
	}
	// The regression harness must not write to the production audit log.
	c.Audit = ""
	s, e := NewService(c)
	if e != nil {
		t.Fatal(e)
	}
	verifyFilesOnly(t, s)
	fixture, e := os.MkdirTemp(c.Roots["work"], ".hostcontrol-files-only-qa-")
	if e != nil {
		t.Fatal(e)
	}
	// Only the uniquely created fixture directory is removed.
	t.Cleanup(func() { os.RemoveAll(fixture) })
	relative := filepath.Base(fixture) + "/marker.txt"
	decodeResult(t, invoke(s, "fs_write", args("workspace", "work", "path", relative, "text", "files-only QA")))
	read := decodeResult(t, invoke(s, "fs_read", args("workspace", "work", "path", relative)))
	if read["text"] != "files-only QA" {
		t.Fatal("allowed file readback differs")
	}
	if invoke(s, "fs_list", args("workspace", "work", "path", filepath.Base(fixture))).IsError {
		t.Fatal("allowed fixture directory could not be listed")
	}
	outside := t.TempDir()
	if e := os.WriteFile(filepath.Join(outside, "secret.txt"), []byte("outside QA marker"), 0600); e != nil {
		t.Fatal(e)
	}
	for _, p := range []string{"../secret.txt", "C:/Windows/win.ini", "D:/secret.txt", "marker.txt:stream"} {
		if !invoke(s, "fs_read", args("workspace", "work", "path", p)).IsError {
			t.Fatalf("outside path accepted: %s", p)
		}
	}
	link := filepath.Join(fixture, "escape")
	if e := makeTestLink(outside, link); e != nil {
		t.Fatal("could not verify directory-link guard:", e)
	}
	for _, op := range []string{"fs_read", "fs_write", "fs_list"} {
		path := filepath.Base(fixture) + "/escape/secret.txt"
		if op == "fs_list" {
			path = filepath.Base(fixture) + "/escape"
		}
		a := args("workspace", "work", "path", path)
		if op == "fs_write" {
			a["path"] = filepath.Base(fixture) + "/escape/new.txt"
			a["text"] = "must not be written"
		}
		if !invoke(s, op, a).IsError {
			t.Fatalf("outside directory-link access accepted: %s", op)
		}
	}
	if _, e := os.Stat(filepath.Join(outside, "new.txt")); !os.IsNotExist(e) {
		t.Fatal("outside link write had a side effect")
	}
}

func verifyFilesOnly(t *testing.T, s *Service) {
	t.Helper()
	if e := s.Enable(1); e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { s.Pause(false) })
	for _, tool := range Tools() {
		if capability(tool.Name) == "" || capability(tool.Name) == "files" {
			continue
		}
		a := map[string]any{}
		// Supply schema-valid arguments, so rejection proves the capability
		// boundary rather than only malformed-request validation.
		properties := tool.InputSchema["properties"].(map[string]any)
		for name, value := range properties {
			typeName := value.(map[string]any)["type"]
			switch typeName {
			case "string":
				a[name] = "unused"
			case "integer":
				a[name] = float64(1)
			case "array":
				a[name] = []any{}
			}
		}
		r := invoke(s, tool.Name, a)
		if !r.IsError || len(r.Content) != 1 || !strings.Contains(r.Content[0].Text, "capability disabled locally") {
			t.Fatalf("%s was not blocked by the capability boundary: %+v", tool.Name, r)
		}
	}
	if !invoke(s, "fs_list", args("workspace", "unlisted", "path", ".")).IsError {
		t.Fatal("unlisted workspace was accepted")
	}
}
