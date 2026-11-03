package prdesk

import (
	"context"
	"path/filepath"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestTheRunningDeskSaysWhyAPRIsWhereItIs(t *testing.T) {
	d, c := memory(t), &clock{}
	apply(t, d, append(clean(c, 0, 7, "lane/a", landing, h1), Tap{c.at(1), 7, h1, machine.TapTapped}, Hold{c.at(2), 7, machine.MoverRoot, "readers first", "coordinator"})...)
	path := filepath.Join(t.TempDir(), socketFile)
	listener, err := listen(path)
	if err != nil {
		t.Fatal(err)
	}
	server := statusServer(d.Store, repo)
	go func() { _ = server.Serve(listener) }()
	t.Cleanup(func() { _ = server.Close() })
	ctx := context.Background()

	var why Why
	if err := ask(ctx, path, "/why/7", &why); err != nil {
		t.Fatal(err)
	}
	if why.State.State != machine.StateHeld || len(why.Holds) != 1 || why.Holds[0].Lifter != string(machine.MoverRoot) {
		t.Fatalf("why = %+v, want #7 held by the coordinator's hold", why)
	}
	var rows []store.PRState
	if err := ask(ctx, path, "/status", &rows); err != nil || len(rows) != 1 || rows[0].Number != 7 {
		t.Fatalf("status = %+v, %v", rows, err)
	}
	if err := ask(ctx, path, "/why/8", &why); err == nil || !strings.Contains(err.Error(), "no row for #8") {
		t.Fatalf("a PR the desk never saw answered %v", err)
	}
	if err := ask(ctx, filepath.Join(t.TempDir(), socketFile), "/status", &rows); err == nil || !strings.Contains(err.Error(), "no PR desk is answering") {
		t.Fatalf("asking with no desk running answered %v", err)
	}
}
