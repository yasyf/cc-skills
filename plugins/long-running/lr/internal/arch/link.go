package arch

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

func DashboardLink(ctx context.Context, run shell.Runner, state string) (string, error) {
	dashboard, err := drive.DashboardURL(state)
	if err != nil {
		return "", err
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
	link, err := url.Parse(dashboard)
	if err != nil {
		return "", err
	}
	link.Host = strings.TrimSuffix(self.Self.DNSName, ".") + ":" + link.Port()
	return link.String(), nil
}
