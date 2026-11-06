package arch

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

func DashboardLink(ctx context.Context, run shell.Runner, state string) (string, error) {
	path := filepath.Join(state, "dashboard.json")
	body, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	var config struct {
		TLSPort int `json:"tls_port"`
	}
	if err := json.Unmarshal(body, &config); err != nil {
		return "", fmt.Errorf("%s: %w", path, err)
	}
	if config.TLSPort == 0 {
		return "", fmt.Errorf("%s names no tls_port, so the dashboard has no https address, and Slack opens a link only from https", path)
	}
	status, err := run.Run(ctx, shell.Command{Argv: []string{"tailscale", "status", "--peers=false", "--json"}, Timeout: 10 * time.Second})
	if err != nil {
		return "", err
	}
	var self struct {
		Self struct {
			DNSName string `json:"DNSName"`
		} `json:"Self"`
	}
	if err := json.Unmarshal([]byte(status.Stdout), &self); err != nil || self.Self.DNSName == "" {
		return "", errors.New("tailscale status --json names no DNSName for this machine")
	}
	return fmt.Sprintf("https://%s:%d/", strings.TrimSuffix(self.Self.DNSName, "."), config.TLSPort), nil
}
