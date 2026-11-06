package orca

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type pinWorld struct {
	t        *testing.T
	store    *store.Store
	claude   string
	runner   string
	releases map[string]string
	requests atomic.Int64
	server   *httptest.Server
	out      bytes.Buffer
}

func digestOf(body string) string {
	sum := sha256.Sum256([]byte(body))
	return hex.EncodeToString(sum[:])
}

func newPinWorld(t *testing.T) *pinWorld {
	t.Helper()
	root := t.TempDir()
	s, err := store.Open(filepath.Join(root, "lr.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	w := &pinWorld{t: t, store: s, claude: filepath.Join(root, "claude"), runner: filepath.Join(root, "runner.json"), releases: map[string]string{}}
	w.server = httptest.NewServer(http.HandlerFunc(func(reply http.ResponseWriter, request *http.Request) {
		w.requests.Add(1)
		body, found := w.releases[request.URL.Path]
		if !found {
			http.NotFound(reply, request)
			return
		}
		_, _ = reply.Write([]byte(body))
	}))
	t.Cleanup(w.server.Close)
	return w
}

func (w *pinWorld) publish(asset, body, sums string) {
	w.releases[asset] = body
	dir := asset[:strings.LastIndex(asset, "/")+1]
	w.releases[dir+sums] = digestOf(body) + "  ./" + asset[len(dir):] + "\n"
}

func (w *pinWorld) macRuns(plugins map[string]string) {
	w.t.Helper()
	entries := map[string]any{}
	for id, version := range plugins {
		entries[id] = []map[string]string{{"scope": "user", "version": version}, {"scope": "project", "version": "0.0.1"}}
		w.macCached(id, version)
		if id == packTool {
			w.macInstalled(version)
		}
	}
	raw, err := json.Marshal(map[string]any{"version": 2, "plugins": entries})
	if err != nil {
		w.t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(w.claude, "plugins"), 0o700); err != nil {
		w.t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(w.claude, "plugins", "installed_plugins.json"), raw, 0o600); err != nil {
		w.t.Fatal(err)
	}
}

func (w *pinWorld) macCached(plugin string, versions ...string) {
	w.t.Helper()
	name, marketplace, _ := strings.Cut(plugin, "@")
	for _, version := range versions {
		if err := os.MkdirAll(filepath.Join(w.claude, "plugins", "cache", marketplace, name, version), 0o700); err != nil {
			w.t.Fatal(err)
		}
	}
}

func (w *pinWorld) macInstalled(version string) {
	w.t.Helper()
	w.macCached(packTool, version)
	link := filepath.Join(w.claude, "long-running", "current")
	if err := os.MkdirAll(filepath.Dir(link), 0o700); err != nil {
		w.t.Fatal(err)
	}
	if err := os.RemoveAll(link); err != nil {
		w.t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(w.claude, "plugins", "cache", "skills", "long-running", version), link); err != nil {
		w.t.Fatal(err)
	}
}

func writeBinrun(t *testing.T, root, version string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Join(root, "bin"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "bin", "lr.binrun"), []byte("#!/usr/bin/env binrun\n{\"version\":{\"static\":\""+version+"\"}}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestTheSpritePackPinFollowsThePackTheMacInstalledNeverItsNewerPlugin(t *testing.T) {
	for label, pinned := range map[string]string{"behind": "0.7.1", "ahead": "0.7.4"} {
		t.Run(label, func(t *testing.T) {
			w := newPinWorld(t)
			w.macRuns(map[string]string{packTool: "0.7.3"})
			w.macInstalled("0.7.2")
			w.publish("/pack--v0.7.2/pack_0.7.2.tar.gz", "pack two", "checksums.txt")
			w.pinned(w.pack(pinned))

			if err := w.move(); err != nil {
				t.Fatal(err)
			}

			if tool := w.tools()[0]; tool.Version != "0.7.2" || tool.SHA256 != digestOf("pack two") {
				t.Fatalf("%+v", tool)
			}
			if want := "lr sprites pin: long-running@skills follows the pack the Mac has installed, 0.7.2, not its plugin 0.7.3, so no Sprite asks the Mac's store desk for a verb it does not serve yet\nlr sprites pin: long-running@skills moved from " + pinned + " to 0.7.2\n"; w.out.String() != want {
				t.Fatal(w.out.String())
			}
		})
	}
}

func TestARollRefusesAPackOrAnLrNewerThanTheOnesTheMacInstalled(t *testing.T) {
	for label, c := range map[string]struct {
		pack, lr string
		refused  []string
	}{
		"level":        {"0.7.2", "0.1075.1", nil},
		"a newer pack": {"0.7.3", "0.1075.1", []string{"pin 0.7.3", "installed, 0.7.2"}},
		"a newer lr":   {"0.7.2", "0.1077.1", []string{"lr 0.1077.1", "lr 0.1075.1"}},
	} {
		t.Run(label, func(t *testing.T) {
			w := newPinWorld(t)
			w.macInstalled("0.7.2")
			writeBinrun(t, filepath.Join(w.claude, "long-running", "current"), "0.1075.1")
			roller := t.TempDir()
			writeBinrun(t, roller, c.lr)
			w.pinned(w.pack(c.pack))

			err := aheadOfMac(w.claude, w.runner, roller)

			if (err == nil) != (c.refused == nil) {
				t.Fatalf("got %v, want refused %t", err, c.refused != nil)
			}
			for _, want := range c.refused {
				if !strings.Contains(err.Error(), want) {
					t.Fatalf("%v does not name %q", err, want)
				}
			}
		})
	}
}

func (w *pinWorld) pinned(tools ...spriteTool) {
	w.t.Helper()
	raw, err := json.Marshal(map[string]any{
		"drive":      "d & e",
		"sprite_cap": 5,
		"orca":       map[string]any{"run": "run_1", "sprite": map[string]any{"launcher": "/l", "env": map[string]string{"WORKER_REF": "dev"}, "tools": tools}},
	})
	if err != nil {
		w.t.Fatal(err)
	}
	if err := os.WriteFile(w.runner, raw, 0o400); err != nil {
		w.t.Fatal(err)
	}
}

func (w *pinWorld) move() error {
	w.t.Helper()
	w.out.Reset()
	return MoveSpritePins(context.Background(), w.store, w.claude, w.runner, &w.out)
}

func (w *pinWorld) tools() []spriteTool {
	w.t.Helper()
	file, err := readRunnerFile(w.runner)
	if err != nil {
		w.t.Fatal(err)
	}
	return file.Orca.Sprite.Tools
}

func (w *pinWorld) pack(version string) spriteTool {
	return spriteTool{Kind: "plugin", Name: "long-running@skills", URL: w.server.URL + "/pack--v" + version + "/pack_" + version + ".tar.gz", SHA256: digestOf(version), Version: version}
}

func TestTheCciASpriteInstallsFollowsTheCCInboxPluginAndEveryOtherEnvKeyStays(t *testing.T) {
	w := newPinWorld(t)
	w.macRuns(map[string]string{"cc-inbox@cc-inbox": "0.49.64"})
	w.publish("/v0.49.64/cci_0.49.64_linux_amd64.tar.gz", "cci sixty four", "checksums.txt")
	raw, err := json.Marshal(map[string]any{"orca": map[string]any{"sprite": map[string]any{"env": map[string]string{
		cciURLEnv: w.server.URL + "/v0.49.58/cci_0.49.58_linux_amd64.tar.gz", cciSHAEnv: digestOf("old"), "WORKER_REF": "dev",
	}, "tools": []spriteTool{}}}})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(w.runner, raw, 0o600); err != nil {
		t.Fatal(err)
	}

	if err := w.move(); err != nil {
		t.Fatal(err)
	}

	file, err := readRunnerFile(w.runner)
	if err != nil {
		t.Fatal(err)
	}
	env := file.Orca.Sprite.Env
	if env[cciURLEnv] != w.server.URL+"/v0.49.64/cci_0.49.64_linux_amd64.tar.gz" || env[cciSHAEnv] != digestOf("cci sixty four") || env["WORKER_REF"] != "dev" {
		t.Fatalf("env = %v", env)
	}
	if len(file.Orca.Sprite.Tools) != 0 {
		t.Fatalf("cci landed among the tools: %+v", file.Orca.Sprite.Tools)
	}
}

func TestAPinMovesToWhatTheMacRunsOnceItsDigestMatchesAndEveryOtherKeyStays(t *testing.T) {
	w := newPinWorld(t)
	w.macRuns(map[string]string{"long-running@skills": "0.7.2", "cc-context@cc-context": "0.91.70"})
	w.publish("/pack--v0.7.2/pack_0.7.2.tar.gz", "pack two", "checksums.txt")
	w.publish("/v0.91.70/ccx_0.91.70_linux_amd64.tar.gz", "ccx seventy", "SHA256SUMS.txt")
	w.pinned(w.pack("0.7.1"), spriteTool{Kind: "binary", Name: "ccx", URL: w.server.URL + "/v0.91.69/ccx_0.91.69_linux_amd64.tar.gz", SHA256: digestOf("old"), Version: "0.91.69", Follows: "cc-context@cc-context"})

	if err := w.move(); err != nil {
		t.Fatal(err)
	}

	tools := w.tools()
	if tools[0] != (spriteTool{Kind: "plugin", Name: "long-running@skills", URL: w.server.URL + "/pack--v0.7.2/pack_0.7.2.tar.gz", SHA256: digestOf("pack two"), Version: "0.7.2"}) {
		t.Fatalf("%+v", tools[0])
	}
	if tools[1] != (spriteTool{Kind: "binary", Name: "ccx", URL: w.server.URL + "/v0.91.70/ccx_0.91.70_linux_amd64.tar.gz", SHA256: digestOf("ccx seventy"), Version: "0.91.70", Follows: "cc-context@cc-context"}) {
		t.Fatalf("%+v", tools[1])
	}
	raw, err := os.ReadFile(w.runner)
	if err != nil {
		t.Fatal(err)
	}
	var whole struct {
		Drive string `json:"drive"`
		Cap   int    `json:"sprite_cap"`
		Orca  struct {
			Run    string `json:"run"`
			Sprite struct {
				Launcher string            `json:"launcher"`
				Env      map[string]string `json:"env"`
			} `json:"sprite"`
		} `json:"orca"`
	}
	if err := json.Unmarshal(raw, &whole); err != nil {
		t.Fatal(err)
	}
	if whole.Drive != "d & e" || whole.Cap != 5 || whole.Orca.Run != "run_1" || whole.Orca.Sprite.Launcher != "/l" || whole.Orca.Sprite.Env["WORKER_REF"] != "dev" {
		t.Fatalf("a key moved: %s", raw)
	}
	info, err := os.Stat(w.runner)
	if err != nil || info.Mode().Perm() != 0o400 {
		t.Fatal(info.Mode(), err)
	}
	left, err := filepath.Glob(w.runner + ".*")
	if err != nil || len(left) != 0 {
		t.Fatal(left, err)
	}
}

func TestAnAssetOffItsChecksumLeavesThePinWhereItWasAndStillMovesTheOthers(t *testing.T) {
	w := newPinWorld(t)
	w.macRuns(map[string]string{"long-running@skills": "0.7.2", "cc-present@cc-present": "0.55.0"})
	w.publish("/pack--v0.7.2/pack_0.7.2.tar.gz", "pack two", "checksums.txt")
	w.releases["/pack--v0.7.2/pack_0.7.2.tar.gz"] = "pack two, truncated"
	w.publish("/v0.55.0/present_0.55.0.tar.gz", "present", "checksums.txt")
	before := w.pack("0.7.1")
	w.pinned(before, spriteTool{Kind: "binary", Name: "cc-present", URL: w.server.URL + "/v0.54.0/present_0.54.0.tar.gz", SHA256: digestOf("old"), Version: "0.54.0", Follows: "cc-present@cc-present"})

	err := w.move()

	var failed *Failed
	if !errors.As(err, &failed) || !strings.Contains(failed.Line, "and its release says "+digestOf("pack two")+", so long-running@skills stays at 0.7.1") {
		t.Fatal(err)
	}
	tools := w.tools()
	if tools[0] != before {
		t.Fatal("a pin off its digest moved", tools[0])
	}
	if tools[1].Version != "0.55.0" {
		t.Fatal("one bad pin held another", tools[1])
	}
}

func TestAVersionWithNoReleaseYetWaitsForTheNextPassWithoutFailing(t *testing.T) {
	w := newPinWorld(t)
	w.macRuns(map[string]string{"long-running@skills": "0.7.2"})
	before := w.pack("0.7.1")
	w.pinned(before)

	if err := w.move(); err != nil {
		t.Fatal(err)
	}

	if w.tools()[0] != before {
		t.Fatal(w.tools()[0])
	}
	if want := "lr sprites pin: long-running@skills stays at 0.7.1: 0.7.2 has no published release yet\n"; w.out.String() != want {
		t.Fatal(w.out.String())
	}
}

func TestAPinFollowsTheReleaseAPluginInstalledAtACommitCarries(t *testing.T) {
	w := newPinWorld(t)
	w.macRuns(map[string]string{"orca@skills": "0.1.11-96b1d1d3d1bf"})
	w.publish("/orca--v0.1.11/orca_0.1.11.tar.gz", "orca eleven", "checksums.txt")
	w.pinned(spriteTool{Kind: "plugin", Name: "orca@skills", URL: w.server.URL + "/orca--v0.1.0/orca_0.1.0.tar.gz", SHA256: digestOf("old"), Version: "0.1.0"})

	if err := w.move(); err != nil {
		t.Fatal(err)
	}

	if tool := w.tools()[0]; tool.Version != "0.1.11" || tool.SHA256 != digestOf("orca eleven") {
		t.Fatalf("%+v", tool)
	}
	if want := "lr sprites pin: orca@skills moved from 0.1.0 to 0.1.11\n"; w.out.String() != want {
		t.Fatal(w.out.String())
	}
}

func TestAChecksumFileThatDoesNotNameTheAssetYetWaitsWithoutFailing(t *testing.T) {
	w := newPinWorld(t)
	w.macRuns(map[string]string{"long-running@skills": "0.7.2"})
	w.releases["/pack--v0.7.2/checksums.txt"] = digestOf("other") + "  ./other.tar.gz\n"
	before := w.pack("0.7.1")
	w.pinned(before)

	if err := w.move(); err != nil {
		t.Fatal(err)
	}

	if w.tools()[0] != before {
		t.Fatal(w.tools()[0])
	}
	if want := "lr sprites pin: long-running@skills stays at 0.7.1: 0.7.2 has no published release yet\n"; w.out.String() != want {
		t.Fatal(w.out.String())
	}
}

func TestAPinTakesTheNewestVersionTheMacHasWithAReleaseWhenItsNewestIsStillPublishing(t *testing.T) {
	w := newPinWorld(t)
	w.macRuns(map[string]string{"long-running@skills": "0.7.12"})
	w.macCached("long-running@skills", "0.7.9", "0.7.10", "0.7.11", "0.7.1", "0.7.1.lock", "0.7.11.digest")
	w.publish("/pack--v0.7.9/pack_0.7.9.tar.gz", "pack nine", "checksums.txt")
	w.publish("/pack--v0.7.10/pack_0.7.10.tar.gz", "pack ten", "checksums.txt")
	w.pinned(w.pack("0.7.9"))

	if err := w.move(); err != nil {
		t.Fatal(err)
	}

	if tool := w.tools()[0]; tool.Version != "0.7.10" || tool.SHA256 != digestOf("pack ten") {
		t.Fatalf("%+v", tool)
	}
	if want := "lr sprites pin: long-running@skills moved from 0.7.9 to 0.7.10\n"; w.out.String() != want {
		t.Fatal(w.out.String())
	}
}

func TestAnAssetOffItsChecksumIsNotPassedOverForAnOlderVersion(t *testing.T) {
	w := newPinWorld(t)
	w.macRuns(map[string]string{"long-running@skills": "0.7.3"})
	w.macCached("long-running@skills", "0.7.2")
	w.publish("/pack--v0.7.3/pack_0.7.3.tar.gz", "pack three", "checksums.txt")
	w.releases["/pack--v0.7.3/pack_0.7.3.tar.gz"] = "pack three, truncated"
	w.publish("/pack--v0.7.2/pack_0.7.2.tar.gz", "pack two", "checksums.txt")
	before := w.pack("0.7.1")
	w.pinned(before)

	var failed *Failed
	if err := w.move(); !errors.As(err, &failed) {
		t.Fatal(err)
	}
	if w.tools()[0] != before {
		t.Fatal("a pin moved past a release off its digest", w.tools()[0])
	}
}

func TestAPinNeverMovesOntoAVersionARollMarkedBad(t *testing.T) {
	w := newPinWorld(t)
	w.macRuns(map[string]string{"long-running@skills": "0.7.2"})
	w.publish("/pack--v0.7.2/pack_0.7.2.tar.gz", "pack two", "checksums.txt")
	if err := w.store.MarkBadTool("long-running@skills", "0.7.2", "its install failed on pool-a", time.Date(2026, 11, 3, 13, 0, 0, 0, time.UTC)); err != nil {
		t.Fatal(err)
	}
	before := w.pack("0.7.1")
	w.pinned(before)

	if err := w.move(); err != nil {
		t.Fatal(err)
	}

	if w.tools()[0] != before {
		t.Fatal(w.tools()[0])
	}
	if w.requests.Load() != 0 {
		t.Fatal("fetched a version marked bad", w.requests.Load())
	}
}

func TestAPassWithNothingToMoveFetchesNothingAndLeavesTheFileAlone(t *testing.T) {
	w := newPinWorld(t)
	w.macRuns(map[string]string{"long-running@skills": "0.7.1"})
	w.pinned(w.pack("0.7.1"), spriteTool{Kind: "binary", Name: "hand-kept", URL: w.server.URL + "/hand-kept.tgz", SHA256: digestOf("x"), Version: "1.0.0"})
	before, err := os.ReadFile(w.runner)
	if err != nil {
		t.Fatal(err)
	}

	if err := w.move(); err != nil {
		t.Fatal(err)
	}

	after, err := os.ReadFile(w.runner)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(before, after) || w.requests.Load() != 0 {
		t.Fatal(w.requests.Load(), string(after))
	}
}

func TestAPinFollowingAPluginTheMacLacksIsARefusalNamingBoth(t *testing.T) {
	w := newPinWorld(t)
	w.macRuns(map[string]string{})
	w.pinned(spriteTool{Kind: "package", Name: "capt-hookd", URL: w.server.URL + "/v1/hook_1.tgz", SHA256: digestOf("x"), Version: "1", Follows: "captain-hook@captain-hook"})

	err := w.move()

	var failed *Failed
	if !errors.As(err, &failed) || failed.Line != "lr sprites pin: capt-hookd follows captain-hook@captain-hook, which this Mac has not installed" {
		t.Fatal(err)
	}
}
