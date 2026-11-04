package orca

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
)

//go:embed sprite_install.sh
var installTools string

var (
	binaryName  = regexp.MustCompile(`\A[a-z0-9][a-z0-9._-]*\z`)
	toolName    = map[string]*regexp.Regexp{"binary": binaryName, "raw": binaryName, "package": binaryName, "plugin": regexp.MustCompile(`\A[a-z0-9][a-z0-9-]*@[a-z0-9][a-z0-9-]*\z`)}
	toolVersion = regexp.MustCompile(`\A[0-9][0-9A-Za-z.+-]*\z`)
	sha256Hex   = regexp.MustCompile(`\A[0-9a-f]{64}\z`)
)

type spriteTool struct {
	Kind    string `json:"kind"`
	Name    string `json:"name"`
	URL     string `json:"url"`
	SHA256  string `json:"sha256"`
	Version string `json:"version"`
	Follows string `json:"follows,omitempty"`
}

const graderTool = "codex-ask"

var hostTools = []string{"long-running@skills", "ccx", graderTool}

type spritePins struct {
	ccURL, ccSHA, lrURL, lrSHA, lrMember string
	tools                                []spriteTool
}

type binrunRelease struct {
	Type string `json:"type"`
	Repo string `json:"repo"`
	Tag  string `json:"tag"`
	Name string `json:"name"`
}

func readPins(env func(string) string) (spritePins, error) {
	p := spritePins{ccURL: env("WORKER_CCI_URL"), ccSHA: env("WORKER_CCI_SHA256")}
	if p.ccURL == "" {
		return p, refuse("set WORKER_CCI_URL to the cci linux_amd64 release archive a Sprite installs")
	}
	if p.ccSHA == "" {
		return p, refuse("set WORKER_CCI_SHA256 to the SHA-256 of that archive")
	}
	if err := p.pinLR(filepath.Join(env("BINRUN_PLUGIN_ROOT"), "bin", "lr.binrun")); err != nil {
		return p, err
	}
	return p, p.pinTools(env("WORKER_TOOLS"))
}

func (p *spritePins) pinLR(descriptor string) error {
	var pin struct {
		Platforms map[string]struct {
			Hash      string          `json:"hash"`
			Digest    string          `json:"digest"`
			Format    string          `json:"format"`
			Path      string          `json:"path"`
			Providers []binrunRelease `json:"providers"`
		} `json:"platforms"`
	}
	raw, err := os.ReadFile(descriptor)
	_, body, _ := bytes.Cut(raw, []byte("\n"))
	if err != nil || json.Unmarshal(body, &pin) != nil {
		return refuse("no lr pin at %s to install on the Sprite; run lr through the pack's bin/lr; nothing was prepared", descriptor)
	}
	linux := pin.Platforms[lrPlatform]
	release := slices.IndexFunc(linux.Providers, func(p binrunRelease) bool { return p.Type == "github-release" })
	if linux.Hash != "sha256" || linux.Format != "tar.gz" || release < 0 {
		return refuse("%s pins no sha256 tar.gz GitHub release of lr for %s; nothing was prepared", descriptor, lrPlatform)
	}
	asset := linux.Providers[release]
	p.lrURL = "https://github.com/" + asset.Repo + "/releases/download/" + asset.Tag + "/" + asset.Name
	p.lrSHA, p.lrMember = linux.Digest, linux.Path
	return nil
}

func (p *spritePins) pinTools(raw string) error {
	if raw == "" {
		return nil
	}
	if err := json.Unmarshal([]byte(raw), &p.tools); err != nil {
		return refuse("WORKER_TOOLS is no JSON list of Sprite tool pins: %v; nothing was prepared", err)
	}
	for _, tool := range p.tools {
		name, known := toolName[tool.Kind]
		if !known || !name.MatchString(tool.Name) || tool.URL == "" || !sha256Hex.MatchString(tool.SHA256) || !toolVersion.MatchString(tool.Version) {
			return refuse("WORKER_TOOLS pins %q, not a binary, raw, package or plugin with a name, url, lowercase SHA-256 and version; nothing was prepared", tool.Name)
		}
	}
	return nil
}

func (p spritePins) forHost() spritePins {
	return p.only(hostTools...)
}

func (p spritePins) only(names ...string) spritePins {
	p.tools = slices.DeleteFunc(slices.Clone(p.tools), func(tool spriteTool) bool { return !slices.Contains(names, tool.Name) })
	return p
}

func (p spritePins) installArgv(workspace string, needs []string) []string {
	return slices.Concat([]string{"sprite", "exec", "-s", workspace, "--no-stdin", "--", "sh", "-c", installTools, "sh"}, p.installWords(needs))
}

func (p spritePins) installScript(needs []string) string {
	words := p.installWords(needs)
	for i, word := range words {
		words[i] = shellQuote(word)
	}
	return "exec 2>&1\nset -- " + strings.Join(words, " ") + "\n" + installTools
}

func (p spritePins) installWords(needs []string) []string {
	pinned := make([]string, 0, 5*len(p.tools))
	for _, tool := range p.tools {
		pinned = append(pinned, tool.Kind, tool.Name, tool.URL, tool.SHA256, tool.Version)
	}
	return slices.Concat([]string{p.ccURL, p.ccSHA, p.lrURL, p.lrSHA, p.lrMember}, pinned, []string{"--"}, needs)
}

func installedTools(stdout string) map[string]string {
	installed := map[string]string{}
	for _, line := range strings.Split(stdout, "\n") {
		if words := strings.Fields(line); len(words) == 3 && words[0] == "installed" {
			installed[words[1]] = words[2]
		}
	}
	return installed
}
