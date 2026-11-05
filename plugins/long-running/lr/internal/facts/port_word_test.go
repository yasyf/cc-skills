package facts_test

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func busRecord(t *testing.T, seq int, lane, kind, text string, attrs map[string]string) client.Record {
	t.Helper()
	encoded, err := json.Marshal(attrs)
	if err != nil {
		t.Fatal(err)
	}
	var record client.Record
	line := fmt.Sprintf(`{"seq":%d,"lane":%q,"kind":%q,"text":%q,"refs":{"prs":[%d]},"fields":{"attrs":%s}}`, seq, lane, kind, text, surfacePR, encoded)
	if err := json.Unmarshal([]byte(line), &record); err != nil {
		t.Fatal(err)
	}
	return record
}

func TestTheRowTheGoTheGateAndTheTapPathReadOneHeadTheSameWay(t *testing.T) {
	const (
		iris        = "go/sand/next/cmd/iris_deploy.go"
		testFile    = "go/sand/next/cmd/router/tls_test.go"
		tapRequired = "TAP-REQUIRED"
	)
	twin := "fidelity: ok " + surfaceHead + " class twin: carries the old tree's deploy checks"
	testOnly := "fidelity: ok " + surfaceHead + " " + gates.TestOnlyGrade + ": ports the test"
	purePort := "fidelity: ok " + surfaceHead + " " + gates.PortGrade + ": compared by file"
	plain := "fidelity: ok " + surfaceHead + " stays in scope"
	clean := map[string]string{gates.PortOfAttr: "48435", gates.PortInteractsAttr: gates.PortCleanWord}
	flake := map[string]string{gates.FlakeFixAttr: "TestChatStream"}
	for name, c := range map[string]struct {
		grades       []string
		claim        map[string]string
		sourceMerged bool
		files        []string
		lands        string
		goRefuses    string
	}{
		"#48819, a clean port the first grader calls a twin":         {[]string{twin, purePort}, clean, true, []string{iris}, prrow.PortLands, ""},
		"#50285, a clean port the first grader calls test-only":      {[]string{testOnly, purePort}, clean, true, []string{testFile}, prrow.PortLands, ""},
		"#50292, a clean port whose only fidelity is an override":    {[]string{"fidelity: missing " + surfaceHead + " a doc line", "fidelity-override #4242 at abc1234: 3. Port of #48435."}, clean, true, []string{iris}, prrow.PortLands, ""},
		"a port its record says interacts lands on his tap":          {[]string{purePort}, map[string]string{gates.PortOfAttr: "48435", gates.PortInteractsAttr: "yes"}, true, []string{iris}, "", "port-interacts=no"},
		"a port silent on interaction lands on his tap":              {[]string{purePort}, map[string]string{gates.PortOfAttr: "48435"}, true, []string{iris}, "", "port-interacts=no"},
		"a port whose source never merged lands on his tap":          {[]string{purePort}, clean, false, []string{iris}, "", "its source #48435 is not a merged pull request"},
		"a port with a file outside the next tree lands on his tap":  {[]string{purePort}, clean, true, []string{"infra/rows/x.ts", iris}, "", "infra/rows/x.ts is outside go/sand/next/"},
		"no claim at all stays on the tap rule":                      {[]string{twin}, nil, true, []string{iris}, "", "tap rule rule/82317b0"},
		"a flake fix in test code lands on his word":                 {[]string{testOnly}, flake, false, []string{testFile}, prrow.FlakeLands, ""},
		"a flake fix touching product code lands on his tap":         {[]string{plain}, flake, false, []string{iris}, "", "flake fix: " + iris + " is not test or fixture code"},
		"#50715, a flake fix whose helper a reader graded test-only": {[]string{testOnly}, flake, false, []string{testFile, "go/sand/next/internal/callcount/callcount.go"}, prrow.FlakeLands, ""},
	} {
		t.Run(name, func(t *testing.T) {
			h := unreadHarness(t)
			h.source.Reads = false

			headAttrs := map[string]string{fmt.Sprintf("head:%d", surfacePR): surfaceHead}
			stored := map[string]string{"read-ci": prrow.CIGreen, "read-approvers": "a", "read-mergeable": string(machine.MergeableClean), "finder": string(machine.FinderClear), "fidelity": string(machine.FidelityPass)}
			for attr, value := range c.claim {
				headAttrs[attr], stored[attr] = value, value
			}
			records := make([]client.Record, 0, 2+len(c.grades))
			records = append(records, busRecord(t, 1, "finder-desk", "review", "finder: ok "+surfaceHead+" no majors", nil), busRecord(t, 2, "port-x", "head", "HEAD", headAttrs))
			for at, grade := range c.grades {
				lane, kind := []string{"fidelity-pipeline", "fidelity-desk"}[at], "review"
				if strings.HasPrefix(grade, "fidelity-override") {
					lane, kind = "root", "decide"
				}
				records = append(records, busRecord(t, 3+at, lane, kind, grade, nil))
			}
			patch := "+retry()"
			patches := map[string]*string{}
			for _, file := range c.files {
				patches[file] = &patch
			}
			claim := gates.Claim{Repo: repo, PR: surfacePR, Head: surfaceHead}
			text, _, refusals := gates.ExemptGo(claim, gates.Read{
				Records: records, Exempt: gates.Grades(nil, []string{"class twin", "class old-tree-port"}, ""),
				TapRules: map[string][]string{"rule/82317b0": {"go/sand/next/*"}}, Files: c.files, Patches: patches, PortSourceMerged: c.sourceMerged,
			})
			said := strings.Join(refusals, "; ")
			if c.goRefuses == "" && (said != "" || !strings.Contains(text, "[exempt: ")) {
				t.Fatalf("the go said %q and refused %q, want a go", text, said)
			}
			if !strings.Contains(said, c.goRefuses) {
				t.Fatalf("the go refused %q, want it to name %q", said, c.goRefuses)
			}

			port, claimed := gates.PortAt(claim, records)
			if claimed {
				for kind, value := range gates.PortEvidence(c.sourceMerged, c.files) {
					stored[kind] = value
				}
			}
			if said != "" && (claimed && !port.Interacts || gates.FlakeAt(claim, records)) {
				stored[gates.StandingRefused] = said
			}
			if err := h.store.PutReviewRows([]store.ReviewRow{{
				Repo: repo, Number: surfacePR, Head: surfaceHead, ReadHead: surfaceHead, Base: "dev", State: prrow.StateOpen, Title: "a change",
				ForUsers: "a user sees it", PlanLine: "plan step 1", Risk: "low",
			}}); err != nil {
				t.Fatal(err)
			}
			at := time.Date(2026, 11, 4, 16, 0, 0, 0, time.UTC)
			putFacts := func(facts map[string]string) {
				t.Helper()
				if err := h.store.Update(func(tx *store.Tx) error {
					for kind, value := range facts {
						if err := tx.PutFact(repo, surfacePR, surfaceHead, kind, value, at); err != nil {
							return err
						}
					}
					return nil
				}); err != nil {
					t.Fatal(err)
				}
			}
			putFacts(stored)

			gate := strings.Join(landing.GateLines(h.snapshot(t)[surfacePR]), "\n")
			row, found, err := h.store.ReviewRow(repo, surfacePR)
			if err != nil || !found {
				t.Fatalf("row %+v, found %v, err %v", row, found, err)
			}
			_, tapRefused, err := prrow.TapAt(h.store, repo, bus.Tap{PR: surfacePR, Head: surfaceHead})
			if err != nil {
				t.Fatal(err)
			}
			if c.lands == "" {
				if !row.ReadyForTap || !strings.Contains(gate, tapRequired) || tapRefused != "" {
					t.Fatalf("on his tap: row ready %v, waits %q; gate %q; tap refused %q", row.ReadyForTap, row.WaitsOn, gate, tapRefused)
				}
				return
			}
			if row.ReadyForTap || row.WaitsOn != c.lands || strings.Contains(gate, tapRequired) || !strings.Contains(gate, gates.NoGo) || tapRefused != c.lands {
				t.Fatalf("on his word: row ready %v, waits %q; gate %q; tap refused %q", row.ReadyForTap, row.WaitsOn, gate, tapRefused)
			}

			putFacts(map[string]string{"exempt": gates.OldTreePortClass})
			if lines := landing.GateLines(h.snapshot(t)[surfacePR]); len(lines) != 0 {
				t.Fatalf("a clean head with its go still printed %q", lines)
			}
		})
	}
}
