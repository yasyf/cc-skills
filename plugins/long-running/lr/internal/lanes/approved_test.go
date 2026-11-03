package lanes

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

func verdict(t *testing.T, seq int64, pr int, said, event string) client.Record {
	t.Helper()
	raw, err := json.Marshal(map[string]any{
		"seq": seq, "at": "2026-11-03T08:00:00Z", "kind": "owner", "lane": boardWatch, "drive": testDrive, "text": said,
		"refs":   map[string]any{"prs": []string{fmt.Sprintf("o/r#%d", pr)}},
		"fields": map[string]any{"attrs": map[string]string{"event": event, "block": "merge-prs", "row": fmt.Sprint(pr), "verdict": said, "head": "e718a74"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	var record client.Record
	if err := json.Unmarshal(raw, &record); err != nil {
		t.Fatal(err)
	}
	return record
}

func TestApprovedSplitsTheNewestApprovalsByWhetherTheirPRIsStillOpen(t *testing.T) {
	bus := &fakeBus{tail: []client.Record{
		verdict(t, 1, 10, "approved", "triage.decided"),
		verdict(t, 2, 11, "approved", "triage.decided"),
		verdict(t, 3, 12, "approved", "triage.decided"),
		verdict(t, 4, 12, "rejected", "triage.decided"),
		verdict(t, 5, 13, "rejected", "triage.decided"),
		verdict(t, 6, 13, "approved", "blanket"),
		verdict(t, 7, 14, "approved", backfillEvent),
	}}
	reads := 0
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive, Repo: "o/r"}, Now: func() time.Time { return t0 }, Runner: scripted(func(argv []string) (shell.Result, error) {
		reads++
		query := strings.Join(argv, " ")
		for _, n := range []string{"10", "11", "13"} {
			contains(t, query, "pullRequest(number: "+n+")")
		}
		return shell.Result{Stdout: `{"data":{"repository":{"p10":{"state":"MERGED"},"p11":{"state":"OPEN"},"p13":{"state":"OPEN"}}}}`}, nil
	})}

	approved, gone, open, err := env.Approved(context.Background(), 40)
	if err != nil {
		t.Fatal(err)
	}

	equal(t, [][]int{approved, gone, open}, [][]int{{10, 11, 13}, {10}, {11, 13}})
	equal(t, reads, 1)
	equal(t, []any{bus.query.After, bus.query.Kinds, bus.query.Lanes}, []any{int64(40), []string{"owner"}, []string{boardWatch}})
}
