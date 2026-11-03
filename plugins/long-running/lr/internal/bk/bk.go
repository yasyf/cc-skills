package bk

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	readTimeout = 5 * time.Minute
	urlFile     = ".config/lr/bk.url"
)

var (
	graphQLNoise    = regexp.MustCompile(`"""[\s\S]*?"""|"(?:[^"\\]|\\.)*"|#[^\n]*`)
	graphQLWriteOps = regexp.MustCompile(`\b(mutation|subscription)\b`)
)

type Settings struct {
	Port      int    `json:"port"`
	ServePort int    `json:"serve_port"`
	AdmitTag  string `json:"admit_tag"`
}

func LoadSettings(path string) (Settings, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Settings{}, err
	}
	var s Settings
	if err := json.Unmarshal(raw, &s); err != nil || s.Port <= 0 || s.ServePort <= 0 || s.AdmitTag == "" {
		return Settings{}, fmt.Errorf("%s needs port, serve_port and admit_tag, the tailnet tag of the machines it answers", path)
	}
	return s, nil
}

type Reply struct {
	Stdout string `json:"stdout"`
	Stderr string `json:"stderr"`
	Code   int    `json:"code"`
}

type Proxy struct {
	Runner   shell.Runner
	CLI      []string
	Checkout string
	AdmitTag string
}

func (p Proxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	caller := r.Header.Get("X-Forwarded-For")
	if caller == "" {
		http.Error(w, "this proxy answers only tailnet callers that tailscale serve forwards", http.StatusForbidden)
		return
	}
	tags, err := p.tags(r.Context(), caller)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	if !slices.Contains(tags, p.AdmitTag) {
		http.Error(w, fmt.Sprintf("%s carries no %s, so it reads no Buildkite here", caller, p.AdmitTag), http.StatusForbidden)
		return
	}
	var args []string
	if err := json.NewDecoder(r.Body).Decode(&args); err != nil {
		http.Error(w, "the body is no JSON list of bk arguments", http.StatusBadRequest)
		return
	}
	if refusal := refused(args); refusal != "" {
		http.Error(w, refusal, http.StatusForbidden)
		return
	}
	result, err := p.Runner.Run(r.Context(), shell.Command{Argv: slices.Concat(p.CLI, args), Dir: p.Checkout, Timeout: readTimeout})
	code := 0
	var exit *shell.ExitError
	switch {
	case errors.As(err, &exit):
		code = exit.Code
	case err != nil:
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(Reply{Stdout: result.Stdout, Stderr: result.Stderr, Code: code})
}

func (p Proxy) tags(ctx context.Context, caller string) ([]string, error) {
	result, err := p.Runner.Run(ctx, shell.Command{Argv: []string{"tailscale", "whois", "--json", caller}, Timeout: time.Minute})
	if err != nil {
		return nil, fmt.Errorf("tailscale whois %s failed: %w", caller, err)
	}
	var who struct {
		Node struct {
			Tags []string `json:"Tags"`
		} `json:"Node"`
	}
	if err := json.Unmarshal([]byte(result.Stdout), &who); err != nil {
		return nil, fmt.Errorf("tailscale whois %s printed no JSON", caller)
	}
	return who.Node.Tags, nil
}

func refused(args []string) string {
	if len(args) == 0 {
		return "name a bk read: api, log or graphql"
	}
	rest := args[1:]
	switch args[0] {
	case "log":
		if len(rest) == 3 {
			return ""
		}
		return "log takes <pipeline> <build> <job>"
	case "api":
		if len(rest) == 2 && (rest[0] == "-no-wait" || rest[0] == "--no-wait") {
			rest = rest[1:]
		}
		if len(rest) != 1 || strings.HasPrefix(rest[0], "-") {
			return "api reads one path with GET, and takes no -X or -d"
		}
		if strings.HasPrefix("/"+strings.TrimPrefix(rest[0], "/"), "/organizations/") {
			return "api reads only the proxy's own organization; drop the /organizations/<org> prefix"
		}
		return ""
	case "graphql":
		if len(rest) == 3 && rest[0] == "-v" {
			rest = rest[2:]
		}
		if len(rest) != 1 {
			return "graphql takes [-v JSON] <query>"
		}
		if graphQLWriteOps.MatchString(graphQLNoise.ReplaceAllString(rest[0], "")) {
			return "graphql runs queries only, never a mutation or subscription"
		}
		return ""
	}
	return fmt.Sprintf("bk %s is no read; the proxy runs api, log and graphql reads only", args[0])
}

func Forward(ctx context.Context, home string, args []string, stdout, stderr io.Writer) (int, error) {
	written, err := os.ReadFile(filepath.Join(home, urlFile))
	if err != nil {
		return 0, fmt.Errorf("no ~/%s names the drive's Buildkite proxy: set WORKER_BK_URL in runner.json's orca.sprite.env, and lr launch worker writes it on each Sprite", urlFile)
	}
	url := strings.TrimSpace(string(written))
	body, err := json.Marshal(args)
	if err != nil {
		return 0, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		return 0, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		said, _ := io.ReadAll(response.Body)
		return 0, fmt.Errorf("%s answered %d: %s", url, response.StatusCode, strings.TrimSpace(string(said)))
	}
	var reply Reply
	if err := json.NewDecoder(response.Body).Decode(&reply); err != nil {
		return 0, fmt.Errorf("%s answered no bk reply: %w", url, err)
	}
	if _, err := io.WriteString(stdout, reply.Stdout); err != nil {
		return 0, err
	}
	if _, err := io.WriteString(stderr, reply.Stderr); err != nil {
		return 0, err
	}
	return reply.Code, nil
}
