//go:build windows && amd64

package host

import (
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
	"unicode/utf16"
)

func inspectWindow(window uintptr) (any, error) {
	if _, e := windowBounds(window); e != nil {
		return nil, e
	}
	// Handle is parsed as an integer before reaching this function. No remote
	// text is interpolated into this fixed UI Automation program.
	script := `$ErrorActionPreference='Stop';[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false);Add-Type -AssemblyName UIAutomationClient;Add-Type -AssemblyName UIAutomationTypes;$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]` + strconv.FormatUint(uint64(window), 10) + `);$q=New-Object System.Collections.Queue;$q.Enqueue($root);$walker=[System.Windows.Automation.TreeWalker]::ControlViewWalker;$items=New-Object System.Collections.Generic.List[object];while($q.Count -gt 0 -and $items.Count -lt 500){$e=$q.Dequeue();try{$c=$e.Current;$b=$c.BoundingRectangle;$items.Add([pscustomobject]@{name=$c.Name;automation_id=$c.AutomationId;control_type=$c.ControlType.ProgrammaticName;enabled=$c.IsEnabled;offscreen=$c.IsOffscreen;left=$b.Left;top=$b.Top;width=$b.Width;height=$b.Height});$child=$walker.GetFirstChild($e);while($null -ne $child -and $q.Count -lt 500){$q.Enqueue($child);$child=$walker.GetNextSibling($child)}}catch{}};ConvertTo-Json -InputObject @($items.ToArray()) -Depth 4 -Compress`
	units := utf16.Encode([]rune(script))
	encoded := make([]byte, len(units)*2)
	for i, u := range units {
		binary.LittleEndian.PutUint16(encoded[2*i:], u)
	}
	program := filepath.Join(os.Getenv("SystemRoot"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
	c := exec.Command(program, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.StdEncoding.EncodeToString(encoded))
	var out, errOut limitedOutput
	c.Stdout = &out
	c.Stderr = &errOut
	g, e := startProcess(c)
	if e != nil {
		return nil, fmt.Errorf("UI Automation backend unavailable")
	}
	defer g.Close()
	done := make(chan error, 1)
	go func() { done <- c.Wait() }()
	select {
	case e = <-done:
	case <-time.After(8 * time.Second):
		g.Close()
		<-done
		return nil, fmt.Errorf("UI Automation timed out; use screenshot tools")
	}
	if e != nil {
		return nil, fmt.Errorf("UI Automation failed for this window; use screenshot tools")
	}
	text, truncated := out.state()
	if truncated {
		return nil, fmt.Errorf("UI Automation output limit exceeded")
	}
	var v any
	if e = json.Unmarshal([]byte(strings.TrimPrefix(text, "\ufeff")), &v); e != nil {
		return nil, fmt.Errorf("invalid UI Automation response")
	}
	return v, nil
}
