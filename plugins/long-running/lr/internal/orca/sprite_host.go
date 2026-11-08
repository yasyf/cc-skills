package orca

import (
	"archive/tar"
	"bytes"
	"cmp"
	"compress/gzip"
	"context"
	_ "embed"
	"encoding/json"
	"fmt"
	"io"
	"maps"
	"os"
	"path"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

//go:embed sprite_host.sh
var hostLayout string

const (
	hostHome        = "/home/sprite"
	hostLR          = hostHome + "/.claude/long-running/current/bin/lr"
	hostRunner      = "lr-landing-runner"
	hostNoCheckout  = "host: no checkout"
	hostNoRelay     = "host: no relay"
	hostNoRecords   = "host: no records"
	ghSecret        = "secret/GH_TOKEN"
	trunkSecret     = "secret/TRUNK_MERGE_API_KEY"
	buildkiteSecret = "secret/BUILDKITE_TOKEN"
	cloneHost       = gitAuthFromStdin + `[ -d "$2/.git" ] || git clone --quiet --filter=blob:none "$1" "$2"`
	seedHost        = `cci relay export --drive "$1" > "$3" && sprite exec -s "$2" --no-stdin --file "$3:$4/store-seed.gz" -- sh -c 'sprite-env services create store-seed --cmd sh --args "-c,$HOME/.local/bin/cci relay seed --drive $1 < $2/store-seed.gz && touch $HOME/$3; rm -f $2/store-seed.gz; sprite-env services delete store-seed" --no-stream' sh "$1" "$4" "$5"; seeded=$?; rm -f "$3"; exit $seeded`
)

type HostMaker struct {
	Runner   shell.Runner
	Store    *store.Store
	State    string
	Config   string
	CCIDrive string
	MacHome  string
	Tmp      string
	VarTmp   string
	Sleep    func(time.Duration)
	Now      func() time.Time
	Out      io.Writer
}

type hostFile struct {
	mode int64
	text string
}

func (h HostMaker) Make(ctx context.Context, box string) error {
	runner := filepath.Join(h.State, "runner.json")
	if err := RollHostPins(ctx, h.Runner, h.Store, runner, box, "the host", h.Now, h.Out); err != nil {
		return err
	}
	env, err := runnerEnv(runner)
	if err != nil {
		return err
	}
	pins, err := readPins(env)
	if err != nil {
		return err
	}
	pack := slices.IndexFunc(pins.tools, func(tool spriteTool) bool { return tool.Name == packTool })
	if pack < 0 {
		return refuse("lr sprites host: runner.json pins no %s, which the host's lr runs from; nothing was laid out", packTool)
	}
	var config map[string]any
	if err := readJSON(h.Config, &config); err != nil {
		return err
	}
	rawRunner, err := drive.ReadMacOnly(runner)
	if err != nil {
		return err
	}
	var host map[string]any
	if err := json.Unmarshal(rawRunner, &host); err != nil {
		return fmt.Errorf("%s: %w", runner, err)
	}
	name, _ := config["drive"].(string)
	checkout, _ := config["checkout"].(string)
	repo, _ := config["repo"].(string)
	if name == "" || checkout == "" || repo == "" {
		return refuse("lr sprites host: %s names no drive, checkout or repo; nothing was laid out", h.Config)
	}
	files, err := hostFiles(name, config, host, box)
	if err != nil {
		return err
	}
	bundle, err := hostBundle(files, h.Now())
	if err != nil {
		return err
	}
	laid, err := h.step(ctx, box, "layout", shell.Command{Argv: []string{"sprite", "exec", "-s", box, "--", "sh", "-c", hostLayout, "sh", name, box, h.MacHome, checkout, pins.tools[pack].Version, h.Tmp, h.VarTmp}, Stdin: bundle, Timeout: spriteInstallTimeout})
	if err != nil {
		return err
	}
	for _, line := range strings.Split(strings.TrimSpace(laid.Stdout), "\n") {
		if line != "" && !strings.HasPrefix(line, "host: ") {
			fmt.Fprintf(h.Out, "lr sprites host: %s: %s\n", box, line)
		}
	}
	if strings.Contains(laid.Stdout, hostNoCheckout) {
		token, err := h.Runner.Run(ctx, shell.Command{Argv: []string{"ccx", "vcs", "auth", "token", repo}})
		if err != nil {
			return refuse("lr sprites host: no token for %s to clone the host's checkout: %s", repo, cmp.Or(lastSaid(token), err.Error()))
		}
		clone := shell.Command{Argv: []string{"sprite", "exec", "-s", box, "--", "sh", "-c", cloneHost, "sh", "https://github.com/" + repo + ".git", path.Join(h.Tmp, hostRunner)}, Stdin: strings.TrimSpace(token.Stdout) + "\n", Timeout: spriteInstallTimeout}
		if _, err := h.step(ctx, box, "checkout clone", clone); err != nil {
			return err
		}
	}
	if strings.Contains(laid.Stdout, hostNoRelay) {
		sponsor := env(sponsorEnv)
		if _, err := joinRelayOn(ctx, h.Runner, h.Sleep, box, sponsor); err != nil {
			return refuse("lr sprites host: %s did not join the cci relay with %s's service key: %v", box, sponsor, err)
		}
		fmt.Fprintf(h.Out, "lr sprites host: %s: relay joined\n", box)
	}
	if strings.Contains(laid.Stdout, hostNoRecords) {
		copied, err := os.CreateTemp("", "store-seed-*.gz")
		if err != nil {
			return err
		}
		_ = copied.Close()
		seeded := path.Join(".claude", "scratch", name+"-landing", "records-seeded")
		if _, err := h.step(ctx, box, "record seed", shell.Command{Argv: []string{"sh", "-c", seedHost, "sh", h.CCIDrive, box, copied.Name(), h.Tmp, seeded}, Timeout: spriteInstallTimeout}); err != nil {
			return err
		}
	}
	fmt.Fprintf(h.Out, "lr sprites host: %s is set up; its lr-landing and bk-desk start by hand, after the switch\n", box)
	return nil
}

func (h HostMaker) step(ctx context.Context, box, name string, c shell.Command) (shell.Result, error) {
	result, err := h.Runner.Run(ctx, c)
	if err != nil {
		return result, refuse("lr sprites host: %s stopped at its %s: %s", box, name, cmp.Or(lastSaid(result), err.Error()))
	}
	return result, nil
}

func hostFiles(name string, config, runner map[string]any, box string) (map[string]hostFile, error) {
	st := path.Join(".claude", "scratch", name+"-landing")
	abs := path.Join(hostHome, st)
	config["movers"] = []string{"landing"}
	config["queue_key"] = hostLR + " desk-creds get " + trunkSecret + " Value"
	host, _ := runner["host"].(map[string]any)
	landing, _ := runner["landing"].(map[string]any)
	if host == nil || landing == nil {
		return nil, refuse("lr sprites host: runner.json has no host or landing section; nothing was laid out")
	}
	token := []string{hostLR, "desk-creds", "get", buildkiteSecret, "Value"}
	host["sprite"] = box
	landing["ci_token_command"] = token
	files := map[string]hostFile{
		path.Join(st, "bin", "gh"): {0o700, "#!/bin/sh\nif [ -z \"${GH_TOKEN:-}\" ] && [ -z \"${GITHUB_TOKEN:-}\" ]; then\n  GH_TOKEN=$(" + hostLR + " desk-creds get " + ghSecret + " Value) || exit 1\n  export GH_TOKEN\nfi\nexec /.sprite/bin/gh \"$@\"\n"},
		path.Join(st, "run.sh"):    {0o700, "#!/bin/sh\nset -eu\nTRUNK_MERGE_API_KEY=$(" + hostLR + " desk-creds get " + trunkSecret + " Value)\nexport TRUNK_MERGE_API_KEY\nexec " + hostLR + " supervisor run --config " + abs + "/lr.json\n"},
	}
	for file, value := range map[string]any{"lr.json": config, "runner.json": runner, "bk.json": map[string]any{"cli": "tools/ci bk", "checkout": config["checkout"], "token_command": token}} {
		text, err := json.MarshalIndent(value, "", "  ")
		if err != nil {
			return nil, err
		}
		files[path.Join(st, file)] = hostFile{0o600, string(text) + "\n"}
	}
	var aws strings.Builder
	profiles, _ := host["aws_profiles"].([]any)
	for i, profile := range profiles {
		if i > 0 {
			aws.WriteString("\n")
		}
		fmt.Fprintf(&aws, "[profile %s]\ncredential_process = curl -sSf http://127.0.0.1:9181/%s\nregion = %s\n", profile, profile, serviceKeyRegion)
	}
	if aws.Len() > 0 {
		files[".aws/config"] = hostFile{0o600, aws.String()}
	}
	if app, ok := host["github_app"].(map[string]any); ok {
		command, err := json.Marshal(app["private_key_command"])
		if err != nil {
			return nil, err
		}
		files[".config/ccx/github-app.toml"] = hostFile{0o600, fmt.Sprintf("name = %s\nclient_id = %s\nprivate_key_command = %s\n", strconv.Quote(fmt.Sprint(app["name"])), strconv.Quote(fmt.Sprint(app["client_id"])), command)}
	}
	return files, nil
}

func hostBundle(files map[string]hostFile, now time.Time) (string, error) {
	var out bytes.Buffer
	zipped := gzip.NewWriter(&out)
	archive := tar.NewWriter(zipped)
	for _, name := range slices.Sorted(maps.Keys(files)) {
		file := files[name]
		if err := archive.WriteHeader(&tar.Header{Name: name, Mode: file.mode, Size: int64(len(file.text)), ModTime: now, Typeflag: tar.TypeReg}); err != nil {
			return "", err
		}
		if _, err := archive.Write([]byte(file.text)); err != nil {
			return "", err
		}
	}
	if err := archive.Close(); err != nil {
		return "", err
	}
	if err := zipped.Close(); err != nil {
		return "", err
	}
	return out.String(), nil
}
