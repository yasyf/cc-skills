package main

import (
	"bytes"
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func selfImageState(t *testing.T) (string, string) {
	t.Helper()
	home := shortHome(t)
	runs := canonicalScope(t)
	t.Setenv("CODEX_ASK_RUNS_DIR", runs)
	return home, runs
}

func selfImageFixture(t *testing.T, data []byte, mode os.FileMode, mtime time.Time) *os.File {
	t.Helper()
	path := filepath.Join(t.TempDir(), "source")
	if err := os.WriteFile(path, data, mode); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(path, mtime, mtime); err != nil {
		t.Fatal(err)
	}
	file, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = file.Close() })
	return file
}

func TestRetainSelfImageSurvivesUnlink(t *testing.T) {
	home, _ := selfImageState(t)
	data := []byte("original executable bytes\n")
	mtime := time.Unix(1_700_000_000, 123_456_789)
	source := selfImageFixture(t, data, 0o555, mtime)
	if err := os.Remove(source.Name()); err != nil {
		t.Fatal(err)
	}
	image, err := retainSelfImage(source)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(image, filepath.Join(home, appDir, "self-images")+string(os.PathSeparator)) {
		t.Fatalf("retained image outside private state: %s", image)
	}
	if got, err := os.ReadFile(image); err != nil || !bytes.Equal(got, data) {
		t.Fatalf("retained bytes = %q, %v", got, err)
	}
	before, err := os.Stat(image)
	if err != nil {
		t.Fatal(err)
	}
	if before.Mode().Perm() != 0o555 || !before.ModTime().Equal(mtime) {
		t.Fatalf("retained metadata = %s, %s", before.Mode(), before.ModTime())
	}
	again, err := retainSelfImage(source)
	if err != nil || again != image {
		t.Fatalf("warm image = %s, %v; want %s", again, err, image)
	}
	after, err := os.Stat(image)
	if err != nil || !os.SameFile(before, after) {
		t.Fatalf("warm reuse replaced image: %v", err)
	}
}

func TestRetainSelfImageSeparatesMetadataGenerations(t *testing.T) {
	selfImageState(t)
	data := []byte("identical executable bytes\n")
	mtime := time.Unix(1_700_000_000, 123_456_789)
	first := selfImageFixture(t, data, 0o755, mtime)
	newTime := selfImageFixture(t, data, 0o755, mtime.Add(time.Nanosecond))
	newMode := selfImageFixture(t, data, 0o555, mtime)
	images := make(map[string]bool)
	for _, source := range []*os.File{first, newTime, newMode} {
		image, err := retainSelfImage(source)
		if err != nil {
			t.Fatal(err)
		}
		if images[image] {
			t.Fatalf("different metadata generations alias %s", image)
		}
		images[image] = true
	}
	for image := range images {
		if got, err := os.ReadFile(image); err != nil || !bytes.Equal(got, data) {
			t.Fatalf("retained generation changed: %q, %v", got, err)
		}
	}
}

func TestRetainSelfImageConcurrentPublication(t *testing.T) {
	selfImageState(t)
	source := selfImageFixture(t, bytes.Repeat([]byte("original executable bytes\n"), 4096), 0o755, time.Unix(1_700_000_000, 123_456_789))
	type result struct {
		path string
		info os.FileInfo
		err  error
	}
	results := make(chan result, 4)
	for range 4 {
		go func() {
			file, err := os.Open(source.Name())
			if err != nil {
				results <- result{err: err}
				return
			}
			defer file.Close()
			image, err := retainSelfImage(file)
			if err != nil {
				results <- result{err: err}
				return
			}
			info, err := os.Stat(image)
			results <- result{path: image, info: info, err: err}
		}()
	}
	completed := make([]result, 4)
	for i := range completed {
		completed[i] = <-results
	}
	for _, next := range completed {
		if next.err != nil {
			t.Fatal(next.err)
		}
	}
	for _, next := range completed[1:] {
		if next.path != completed[0].path || !os.SameFile(completed[0].info, next.info) {
			t.Fatalf("concurrent publication replaced image: %s", next.path)
		}
	}
}

func TestRetainSelfImageRefusesChangedCache(t *testing.T) {
	for _, mutation := range []string{"bytes", "mode", "mtime", "symlink"} {
		t.Run(mutation, func(t *testing.T) {
			selfImageState(t)
			mtime := time.Unix(1_700_000_000, 123_456_789)
			source := selfImageFixture(t, []byte("original executable bytes\n"), 0o755, mtime)
			image, err := retainSelfImage(source)
			if err != nil {
				t.Fatal(err)
			}
			switch mutation {
			case "bytes":
				if err := os.WriteFile(image, []byte("modified executable bytes\n"), 0o755); err != nil {
					t.Fatal(err)
				}
				if err := os.Chtimes(image, mtime, mtime); err != nil {
					t.Fatal(err)
				}
			case "mode":
				if err := os.Chmod(image, 0o655); err != nil {
					t.Fatal(err)
				}
			case "mtime":
				if err := os.Chtimes(image, mtime, mtime.Add(time.Second)); err != nil {
					t.Fatal(err)
				}
			case "symlink":
				if err := os.Remove(image); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(source.Name(), image); err != nil {
					t.Fatal(err)
				}
			}
			before, err := os.Lstat(image)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := retainSelfImage(source); err == nil {
				t.Fatal("changed retained image was accepted")
			}
			after, err := os.Lstat(image)
			if err != nil || !os.SameFile(before, after) || before.Mode() != after.Mode() || !before.ModTime().Equal(after.ModTime()) {
				t.Fatalf("changed cache was overwritten: %v", err)
			}
		})
	}
}

func TestNonforkCLIDoesNotResolveRetainedImage(t *testing.T) {
	home, runs := selfImageState(t)
	bin := codexAskBin(t)
	source, err := os.Open(bin)
	if err != nil {
		t.Fatal(err)
	}
	defer source.Close()
	image, err := retainSelfImage(source)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(image, 0o600); err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"--version", "--ps", "--help"} {
		command := exec.Command(bin, mode)
		command.Env = dispatchEnv(home, "", runs, t.TempDir(), canonicalScope(t))
		output, err := command.CombinedOutput()
		if err != nil {
			t.Fatalf("nonfork %s consulted broken retained image: %v\n%s", mode, err, output)
		}
		if mode == "--ps" && len(bytes.TrimSpace(output)) != 0 {
			t.Fatalf("retained images leaked into run registry: %s", output)
		}
	}
}

func TestRetainedDispatchKeepsResolvedResourceRootAfterRemoval(t *testing.T) {
	home, runs := selfImageState(t)
	stubDir := canonicalScope(t)
	scope := canonicalScope(t)
	pluginRoot := canonicalScope(t)
	if err := os.MkdirAll(filepath.Join(pluginRoot, "bin"), 0o700); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(codexAskBin(t))
	if err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(pluginRoot, "bin", "codex-ask")
	mtime := time.Unix(1_700_000_000, 123_456_789)
	if err := os.WriteFile(source, data, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(source, mtime, mtime); err != nil {
		t.Fatal(err)
	}
	writePluginManifest(t, pluginRoot)
	writeFile(t, filepath.Join(pluginRoot, "AGENTS.md"), "resolved-origin developer instructions\n")
	foreign := canonicalScope(t)
	if err := os.MkdirAll(filepath.Join(foreign, "bin"), 0o700); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(foreign, "bin", "codex-ask")
	if err := os.Symlink(source, link); err != nil {
		t.Fatal(err)
	}
	writeStub(t, stubDir, "#!/bin/sh\n"+
		"if [ \"$1\" = mcp ]; then\n"+
		"  : > \"$HOME/mcp-ready\"\n"+
		"  while [ ! -f \"$HOME/mcp-release\" ]; do sleep 0.01; done\n"+
		"  printf '[]'; exit 0\n"+
		"fi\n"+
		"ps -p \"$PPID\" -o command= > \"$HOME/worker-command\"\n"+stubCodexReplyBody)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	var stdout, stderr bytes.Buffer
	command := exec.CommandContext(ctx, link, "ping")
	command.Dir = scope
	command.Env = dispatchEnv(home, "", runs, stubDir, scope)
	command.Stdout, command.Stderr = &stdout, &stderr
	started := time.Now()
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	waited := false
	defer func() {
		cancel()
		if !waited {
			_ = command.Wait()
		}
	}()
	deadline := time.Now().Add(10 * time.Second)
	for !exists(filepath.Join(home, "mcp-ready")) {
		if time.Now().After(deadline) {
			t.Fatal("dispatch did not reach private MCP barrier")
		}
		time.Sleep(10 * time.Millisecond)
	}
	if err := os.Remove(source); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(link); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(link, []byte("#!/bin/sh\nexit 91\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Join(home, "mcp-release"), "release\n")
	err = command.Wait()
	waited = true
	if err != nil {
		t.Fatalf("retained dispatch: %v\nstderr: %s", err, stderr.String())
	}
	cold := time.Since(started)
	reply := stdoutLine(stdout.String(), "REPLY_FILE: ")
	sdir := filepath.Dir(reply)
	t.Cleanup(func() { killLane(sdir) })
	if got := strings.TrimSpace(readFile(reply)); got != "pong" {
		t.Fatalf("reply = %q, want pong", got)
	}
	if got := developerInstructions(t, sdir); got != "resolved-origin developer instructions\n\n"+boundedFilesystemDiscovery {
		t.Fatalf("resolved-origin instructions = %q", got)
	}
	if await := stdoutLine(stdout.String(), "AWAIT: "); !strings.Contains(await, link) {
		t.Fatalf("logical recovery path changed: %s", await)
	}
	worker := strings.TrimSpace(readFile(filepath.Join(home, "worker-command")))
	if !strings.Contains(worker, " --worker "+sdir+" "+link+" "+source) {
		t.Fatalf("worker lost original identities: %s", worker)
	}
	image := strings.Fields(worker)[0]
	if !strings.HasPrefix(image, filepath.Join(home, appDir, "self-images")+string(os.PathSeparator)) {
		t.Fatalf("worker did not execute retained image: %s", worker)
	}
	before, err := os.Stat(image)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(source, data, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(source, mtime, mtime); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(link); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(source, link); err != nil {
		t.Fatal(err)
	}
	warm := exec.CommandContext(ctx, link, "ping")
	warm.Dir = scope
	warm.Env = command.Env
	started = time.Now()
	if output, err := warm.CombinedOutput(); err != nil {
		t.Fatalf("warm retained dispatch: %v\n%s", err, output)
	}
	warmCost := time.Since(started)
	after, err := os.Stat(image)
	if err != nil || !os.SameFile(before, after) {
		t.Fatalf("warm dispatch replaced retained image: %v", err)
	}
	t.Logf("self-image dispatch cost: cold_with_unlink_barrier=%s warm=%s image_bytes=%d", cold, warmCost, len(data))
}
