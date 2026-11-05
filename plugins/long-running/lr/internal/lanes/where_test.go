package lanes

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	chatVersionSHA  = "43caf9bff798"
	carryingCommit  = "ddaac718dd53"
	olderCommit     = "6bd62f8f9c5c"
	chatVersionFile = "api/migrations/0463_iris_sessions_chat_version.sql\napi/src/entities/IrisSession.ts\n"
	settledCut      = `{"id":"releases/2026-11-03/40","name":"delicate-basho","mode":"release","status":"converged","build":"https://buildkite.com/forge/release/builds/12389","commit":"6e8d0916e771","components":["sand"],"envs":["core-usw2-auto"],"timestamps":{"running":"2026-11-03T19:41:29.000Z","converged":"2026-11-03T19:59:00.182Z"}}`
)

var whereNow = time.Date(2026, 11, 3, 21, 15, 0, 0, time.UTC)

func chatVersionCut(status string) string {
	return fmt.Sprintf(`{"id":"releases/2026-11-03/44","name":"pensive-vallespir","mode":"release","status":%q,"build":"https://buildkite.com/forge/release/builds/12404","commit":%q,"components":["api","desktop"],"envs":["plat-usw2-prod","core-usw2-auto"],"timestamps":{"running":"2026-11-03T21:08:58.000Z"}}`, status, carryingCommit)
}

func deployRow(target, env, commit, since string) string {
	if commit == carryingCommit {
		return fmt.Sprintf(`{"target":%q,"env":%q,"release":"releases/2026-11-03/44","name":"pensive-vallespir","build":"https://buildkite.com/forge/release/builds/12404","commit":%q,"since":%q}`, target, env, commit, since)
	}
	return fmt.Sprintf(`{"target":%q,"env":%q,"release":"releases/2026-11-03/42","name":"luminous-nwabueze","build":"https://buildkite.com/forge/release/builds/12398","commit":%q,"since":%q}`, target, env, commit, since)
}

func jsonList(rows ...string) string {
	return "[" + strings.Join(rows, ",") + "]"
}

type whereReads struct {
	live, status string
	liveFails    error
	reads        map[string]int
}

func (f *whereReads) read(now time.Time) *ReleaseRead {
	f.reads = map[string]int{}
	return &ReleaseRead{CLI: []string{"tools/ci", "release"}, Token: bkread.Token{Command: []string{tokenTool}}, Checkout: "/checkout", Now: func() time.Time { return now }, Runner: commanded(func(c shell.Command) (shell.Result, error) {
		argv := c.Argv
		tail := argv[max(0, len(argv)-2):]
		switch {
		case argv[0] == tokenTool:
			return shell.Result{Stdout: "read-token\n"}, nil
		case argv[0] == "git" && argv[3] == "diff-tree":
			return shell.Result{Stdout: chatVersionFile}, nil
		case argv[0] == "git" && argv[3] == "merge-base":
			if argv[len(argv)-1] == carryingCommit {
				return shell.Result{}, nil
			}
			return shell.Result{Code: 1}, &shell.ExitError{Argv: argv, Code: 1}
		case argv[0] == "git":
			return shell.Result{}, nil
		case slices.Equal(tail, []string{"select", "--pr"}):
			return shell.Result{Stdout: `{"touched":["api","desktop"]}`}, nil
		case slices.Equal(tail, []string{"live", "--json"}):
			f.reads["live"]++
			if f.liveFails != nil {
				return shell.Result{}, f.liveFails
			}
			return shell.Result{Stdout: f.live}, nil
		case slices.Equal(tail, []string{"status", "--json"}):
			f.reads["status"]++
			return shell.Result{Stdout: f.status}, nil
		}
		return shell.Result{}, fmt.Errorf("no reply scripted for %q", argv)
	})}
}

func (f *whereReads) locate(now time.Time) Where {
	read := f.read(now)
	return read.Locate(context.Background(), read.View(context.Background()), chatVersionSHA)
}

func TestTheChatVersionRowReadsTheReleaseCarryingItAndItsLiveTime(t *testing.T) {
	behindAPI := deployRow("api", "plat-usw2-prod", olderCommit, "2026-11-03T20:44:54Z")
	behindStaging := deployRow("api", "plat-usw2-staging", olderCommit, "2026-11-03T18:16:25Z")
	behindDesktop := deployRow("desktop", "core-usw2-auto", olderCommit, "2026-11-03T21:08:45Z")
	liveAPI := deployRow("api", "plat-usw2-prod", carryingCommit, "2026-11-03T21:30:00Z")
	liveDesktop := deployRow("desktop", "core-usw2-auto", carryingCommit, "2026-11-03T21:40:00Z")
	for _, test := range []struct {
		name, live, status, want string
		now                      time.Time
	}{
		{
			name:   "no release holds the commit",
			live:   jsonList(behindAPI, behindDesktop),
			status: jsonList(settledCut),
			now:    whereNow,
			want:   "landed, no release has cut it yet",
		},
		{
			name:   "a release still queued holds it",
			live:   jsonList(behindAPI, behindDesktop),
			status: jsonList(chatVersionCut("queued"), settledCut),
			now:    whereNow,
			want:   "release 12404 queued",
		},
		{
			name:   "the only release that cut it failed before applying",
			live:   jsonList(behindAPI, behindDesktop),
			status: jsonList(chatVersionCut("failed"), settledCut),
			now:    whereNow,
			want:   "landed; release 12404 cut it and failed, so no release carries it now",
		},
		{
			name:   "a release that leaves out a target the commit touches names it",
			live:   jsonList(behindAPI, behindDesktop),
			status: jsonList(strings.Replace(chatVersionCut("running"), `"components":["api","desktop"]`, `"components":["api"]`, 1), settledCut),
			now:    whereNow,
			want:   "release 12404 running, ETA 1:26 PM; it leaves out desktop",
		},
		{
			name:   "a running release holds it and the last converged one times it",
			live:   jsonList(behindAPI, behindStaging, behindDesktop),
			status: jsonList(chatVersionCut("running"), settledCut),
			now:    whereNow,
			want:   "release 12404 running, ETA 1:26 PM",
		},
		{
			name:   "a release running past the usual time names why it has no ETA",
			live:   jsonList(behindAPI, behindDesktop),
			status: jsonList(chatVersionCut("running"), settledCut),
			now:    whereNow.Add(time.Hour),
			want:   "release 12404 running, ETA unknown: it has run past the 17 minutes a recent release took",
		},
		{
			name:   "every env the release deploys holds it, and staging is not one of them",
			live:   jsonList(liveAPI, behindStaging, liveDesktop),
			status: jsonList(chatVersionCut("converged"), settledCut),
			now:    whereNow.Add(time.Hour),
			want:   "in release 12404, live at 1:40 PM",
		},
		{
			name:   "the API alone is not live for a local chat while the desktop app runs an older build",
			live:   jsonList(liveAPI, behindDesktop),
			status: jsonList(chatVersionCut("running"), settledCut),
			now:    whereNow,
			want:   "release 12404 running, ETA 1:26 PM",
		},
		{
			name:   "a settled release that left the desktop app behind says so",
			live:   jsonList(liveAPI, behindDesktop),
			status: jsonList(chatVersionCut("converged"), settledCut),
			now:    whereNow.Add(time.Hour),
			want:   "in release 12404, not live on desktop/core-usw2-auto",
		},
		{
			name:   "a release older than the recent read counts the envs it reached",
			live:   jsonList(liveAPI, behindStaging),
			status: jsonList(settledCut),
			now:    whereNow.Add(time.Hour),
			want:   "in release 12404, live at 1:30 PM on 1 of 2 envs",
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			reads := whereReads{live: test.live, status: test.status}
			if got := reads.locate(test.now).Line(); got != test.want {
				t.Fatalf("line = %q, want %q", got, test.want)
			}
		})
	}
}

func TestWhereNamesEachTargetAndTheMigrationsTheCommitCarries(t *testing.T) {
	reads := whereReads{
		live:   jsonList(deployRow("api", "plat-usw2-prod", carryingCommit, "2026-11-03T21:30:00Z"), deployRow("api", "tnt-usw2-1frg9c7", olderCommit, "2026-11-03T21:07:57Z")),
		status: jsonList(chatVersionCut("running"), settledCut),
	}

	lines := reads.locate(whereNow).Lines()

	for _, want := range []string{
		"release: 12404 pensive-vallespir (releases/2026-11-03/44) running",
		"target api: live on plat-usw2-prod since 1:30 PM; not on tnt-usw2-1frg9c7",
		"target desktop: unknown: the release ledger names no deploy of it",
		"migrations: 0463_iris_sessions_chat_version.sql",
	} {
		if !slices.Contains(lines, want) {
			t.Errorf("no %q in %q", want, lines)
		}
	}
}

func TestAFailedLiveReadPrintsUnknownWithWhyAndKeepsWhatItCouldRead(t *testing.T) {
	reads := whereReads{status: jsonList(chatVersionCut("running"), settledCut), liveFails: errors.New("the operator role expired")}

	where := reads.locate(whereNow)

	if line := where.Line(); !strings.HasPrefix(line, "unknown: what is live: ") || !strings.Contains(line, "the operator role expired") {
		t.Fatalf("line = %q", line)
	}
	if !slices.Contains(where.Lines(), "migrations: 0463_iris_sessions_chat_version.sql") {
		t.Fatalf("lines = %q", where.Lines())
	}
}

func TestAPassReadsTheReleaseOnceHoweverManyChangesItLocates(t *testing.T) {
	for _, changes := range []int{1, scaletest.N()} {
		reads := whereReads{live: jsonList(deployRow("api", "plat-usw2-prod", carryingCommit, "2026-11-03T21:30:00Z")), status: jsonList(chatVersionCut("running"), settledCut)}
		read := reads.read(whereNow)
		view := read.View(context.Background())
		for change := range changes {
			read.Locate(context.Background(), view, fmt.Sprintf("%012d", change))
		}
		if reads.reads["live"] != 1 || reads.reads["status"] != 1 {
			t.Fatalf("%d changes read the release %v", changes, reads.reads)
		}
	}
}
