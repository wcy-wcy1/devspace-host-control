package host

import (
	"fmt"
	"math"
	"strings"
)

func validateArguments(name string, a map[string]any) error {
	var tool *Tool
	for _, t := range Tools() {
		if t.Name == name {
			copy := t
			tool = &copy
			break
		}
	}
	if tool == nil {
		return fmt.Errorf("unknown tool")
	}
	properties := tool.InputSchema["properties"].(map[string]any)
	for _, k := range tool.InputSchema["required"].([]string) {
		if _, ok := a[k]; !ok {
			return fmt.Errorf("missing argument %s", k)
		}
	}
	for k, v := range a {
		schema, ok := properties[k].(map[string]any)
		if !ok {
			return fmt.Errorf("unknown argument %s", k)
		}
		switch schema["type"] {
		case "string":
			if s, ok := v.(string); !ok || len(s) > 1<<20 {
				return fmt.Errorf("invalid string argument %s", k)
			}
		case "integer":
			f, ok := v.(float64)
			if !ok || math.IsNaN(f) || math.IsInf(f, 0) || math.Trunc(f) != f || math.Abs(f) > 2147483647 {
				return fmt.Errorf("integer argument required for %s", k)
			}
		case "array":
			list, ok := v.([]any)
			if !ok || len(list) > 256 {
				return fmt.Errorf("invalid array %s", k)
			}
			for _, e := range list {
				if s, ok := e.(string); !ok || len(s) > 65536 {
					return fmt.Errorf("string array required for %s", k)
				}
			}
		}
	}
	return nil
}

func readOnlyTool(name string) bool {
	return name == "host_status" || name == "session_info" || name == "fs_read" || name == "fs_list" || name == "exec_poll" || name == "desktop_capture" || name == "desktop_windows" || name == "desktop_inspect"
}
func validToolName(name string) bool {
	if len(name) > 64 {
		return false
	}
	for _, c := range name {
		if !strings.ContainsRune("abcdefghijklmnopqrstuvwxyz_", c) {
			return false
		}
	}
	return name != ""
}
