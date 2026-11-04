package profile

import (
	"context"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestASocketAnswersHeapProfilesOnlyToItsOwner(t *testing.T) {
	dir, err := os.MkdirTemp("", "lr-pprof")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(dir) })
	path := filepath.Join(dir, "pprof.sock")
	if err := os.WriteFile(path, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	stop, err := Serve(path, slog.Default())
	if err != nil {
		t.Fatalf("a stale socket path should be replaced: %v", err)
	}
	defer stop()
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("socket mode = %o, want 600", info.Mode().Perm())
	}
	client := http.Client{Transport: &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		var dialer net.Dialer
		return dialer.DialContext(ctx, "unix", path)
	}}}
	resp, err := client.Get("http://profile/debug/pprof/heap?debug=1")
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = resp.Body.Close() }()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	if resp.StatusCode != http.StatusOK || !strings.Contains(string(body), "heap profile") {
		t.Fatalf("status %d, body %.200s, want a heap profile", resp.StatusCode, body)
	}
}
