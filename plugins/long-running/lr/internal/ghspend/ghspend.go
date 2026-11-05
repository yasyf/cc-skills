package ghspend

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	thisHost    = "this host"
	topCallers  = 6
	readTimeout = 20 * time.Second
)

var (
	appCore    = regexp.MustCompile(`^app#.*:core$`)
	appGraphQL = regexp.MustCompile(`^app.*:graphql$`)
)

type ledger map[string]struct {
	Callers map[string]int `json:"callers"`
}

type Host struct {
	Name string
	Path string
}

type Spend struct {
	Host   string `json:"host"`
	Pool   string `json:"pool"`
	Caller string `json:"caller"`
	Calls  int    `json:"calls"`
}

type Report struct {
	Charged map[string]int `json:"charged"`
	Top     []Spend        `json:"top"`
	Unread  []string       `json:"unread"`
}

type Reader struct {
	Runner shell.Runner
	State  string
}

func Command(a *app.App) *cobra.Command {
	var state string
	var hosts []string
	cmd := &cobra.Command{
		Use:   "gh-spend --state DIR [--host SPRITE=LEDGER]...",
		Short: "Name who spent GitHub calls since the last read, from this machine's ccx ledger and each named Sprite's, as JSON",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			parsed, err := parseHosts(hosts)
			if err != nil {
				return err
			}
			local, err := os.UserCacheDir()
			if err != nil {
				return err
			}
			report, err := Reader{Runner: a.Runner, State: state}.Read(c.Context(), filepath.Join(local, "cc-context", "github-quota", "ledger.json"), parsed)
			if err != nil {
				return err
			}
			return json.NewEncoder(c.OutOrStdout()).Encode(report)
		},
	}
	cmd.Flags().StringVar(&state, "state", "", "the directory holding each host's last read")
	cmd.Flags().StringArrayVar(&hosts, "host", nil, "a Sprite and the path of its ledger, as SPRITE=PATH; repeatable")
	if err := cmd.MarkFlagRequired("state"); err != nil {
		panic(err)
	}
	return cmd
}

func parseHosts(flags []string) ([]Host, error) {
	hosts := make([]Host, 0, len(flags))
	for _, flag := range flags {
		name, path, ok := strings.Cut(flag, "=")
		if !ok || name == "" || path == "" {
			return nil, fmt.Errorf("--host %q is not SPRITE=PATH", flag)
		}
		hosts = append(hosts, Host{Name: name, Path: path})
	}
	return hosts, nil
}

func (r Reader) Read(ctx context.Context, localPath string, hosts []Host) (Report, error) {
	report := Report{Charged: map[string]int{}, Top: []Spend{}, Unread: []string{}}
	local, err := os.ReadFile(localPath)
	if err != nil {
		return report, fmt.Errorf("read this machine's ledger: %w", err)
	}
	if err := r.account(&report, thisHost, local); err != nil {
		return report, err
	}
	for _, host := range hosts {
		read, cancel := context.WithTimeout(ctx, readTimeout)
		result, err := r.Runner.Run(read, shell.Command{Argv: []string{"sprite", "exec", "-s", host.Name, "--no-stdin", "--", "cat", host.Path}})
		cancel()
		if err != nil {
			report.Unread = append(report.Unread, host.Name)
			if err := r.carry(&report, host.Name); err != nil {
				return report, err
			}
			continue
		}
		if err := r.account(&report, host.Name, []byte(result.Stdout)); err != nil {
			return report, err
		}
	}
	slices.SortStableFunc(report.Top, func(a, b Spend) int {
		return cmp.Or(cmp.Compare(b.Calls, a.Calls), cmp.Compare(a.Host, b.Host), cmp.Compare(a.Caller, b.Caller))
	})
	report.Top = report.Top[:min(topCallers, len(report.Top))]
	return report, nil
}

func (r Reader) account(report *Report, host string, raw []byte) error {
	var now ledger
	if err := json.Unmarshal(raw, &now); err != nil {
		return fmt.Errorf("%s's ledger: %w", host, err)
	}
	was, err := r.last(host)
	if err != nil {
		return err
	}
	for pool, entry := range now {
		for caller, calls := range entry.Callers {
			spent := calls - was[pool].Callers[caller]
			if spent < 0 {
				spent = calls
			}
			if spent > 0 {
				report.Top = append(report.Top, Spend{Host: host, Pool: pool, Caller: caller, Calls: spent})
			}
		}
	}
	addCharged(report, now)
	return os.WriteFile(r.snapshot(host), raw, 0o600)
}

func (r Reader) carry(report *Report, host string) error {
	was, err := r.last(host)
	if err != nil {
		return err
	}
	addCharged(report, was)
	return nil
}

func (r Reader) last(host string) (ledger, error) {
	raw, err := os.ReadFile(r.snapshot(host))
	if errors.Is(err, fs.ErrNotExist) {
		return ledger{}, nil
	}
	if err != nil {
		return nil, err
	}
	var was ledger
	if err := json.Unmarshal(raw, &was); err != nil {
		return nil, fmt.Errorf("%s's last read: %w", host, err)
	}
	return was, nil
}

func (r Reader) snapshot(host string) string {
	return filepath.Join(r.State, "ledger.prev."+strings.ReplaceAll(host, " ", "-")+".json")
}

func addCharged(report *Report, entries ledger) {
	for pool, entry := range entries {
		key := ""
		switch {
		case pool == "user:core", pool == "user:graphql":
			key = pool
		case appCore.MatchString(pool):
			key = "app:core"
		case appGraphQL.MatchString(pool):
			key = "app:graphql"
		default:
			continue
		}
		for _, calls := range entry.Callers {
			report.Charged[key] += calls
		}
	}
}
