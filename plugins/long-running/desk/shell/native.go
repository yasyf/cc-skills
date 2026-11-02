package shell

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode/utf8"
)

// NativeHome is cci.native_home.
func NativeHome(env map[string]string, cwd string, getenv func(string) string) string {
	lookup := func(name string) string {
		if value, ok := env[name]; ok {
			return value
		}
		return getenv(name)
	}
	home := lookup("CCI_HOME")
	if home == "" {
		home = filepath.Join(lookup("HOME"), ".cc-inbox")
	}
	if cwd == "" {
		var err error
		if cwd, err = os.Getwd(); err != nil {
			panic(err)
		}
	}
	if filepath.IsAbs(home) {
		return filepath.Clean(home)
	}
	return filepath.Join(cwd, home)
}

// NativeTail is cci.native_tail: a cursor-free tail read through the cci daemon's native handler; true when the read budget expired.
func NativeTail(argv []string, timeout int, env map[string]string, cwd string, getenv func(string) string, monotonic func() float64) (Done, bool) {
	lookup := func(name string) string {
		if value, ok := env[name]; ok {
			return value
		}
		return getenv(name)
	}
	session := lookup("CLAUDE_CODE_SESSION_ID")
	if session == "" {
		session = lookup("CODEX_THREAD_ID")
	}
	request, err := json.Marshal(map[string]any{"argv": argv, "session": session, "agent": lookup("CCI_AGENT_ID"), "home": NativeHome(env, cwd, getenv)})
	if err != nil {
		panic(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Second)
	defer cancel()
	post, err := http.NewRequestWithContext(ctx, http.MethodPost, "http://127.0.0.1:7377/v1/tail", bytes.NewReader(request))
	if err != nil {
		panic(err)
	}
	post.Header.Set("Content-Type", "application/json")
	response, err := http.DefaultClient.Do(post)
	if errors.Is(err, context.DeadlineExceeded) {
		return Done{}, true
	}
	if err != nil {
		return Done{1, "", fmt.Sprintf("cci: native tail: %s\n", err)}, false
	}
	defer response.Body.Close()
	packet, err := io.ReadAll(response.Body)
	if errors.Is(err, context.DeadlineExceeded) {
		return Done{}, true
	}
	if err != nil {
		return Done{1, "", fmt.Sprintf("cci: native tail: %s\n", err)}, false
	}
	var result struct {
		Error      string `json:"error"`
		ReturnCode int    `json:"returncode"`
		Stdout     string `json:"stdout_b64"`
		Stderr     string `json:"stderr_b64"`
	}
	if err := json.Unmarshal(packet, &result); err != nil {
		panic(err)
	}
	if response.StatusCode != http.StatusOK {
		return Done{1, "", fmt.Sprintf("cci: %s\n", result.Error)}, false
	}
	return Done{result.ReturnCode, tailOutput(result.Stdout), tailOutput(result.Stderr)}, false
}

func tailOutput(encoded string) string {
	raw, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		panic(err)
	}
	return universalNewlines(Replace(raw))
}

func universalNewlines(text string) string {
	return strings.ReplaceAll(strings.ReplaceAll(text, "\r\n", "\n"), "\r", "\n")
}

// SplitLines is Python's str.splitlines().
func SplitLines(text string) []string {
	var lines []string
	for len(text) > 0 {
		end, next := len(text), len(text)
		for i, r := range text {
			if r == '\r' && strings.HasPrefix(text[i:], "\r\n") {
				end, next = i, i+2
				break
			}
			if strings.ContainsRune("\n\r\v\f\x1c\x1d\x1e\u0085  ", r) {
				end, next = i, i+utf8.RuneLen(r)
				break
			}
		}
		lines = append(lines, text[:end])
		text = text[next:]
	}
	return lines
}
