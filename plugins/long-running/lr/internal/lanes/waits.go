package lanes

import (
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	redClassFact = "red-class"
	finderDesk   = "finder-desk"
	fidelityDesk = "fidelity-desk"
	headShown    = 10
)

var namedPR = regexp.MustCompile(`#(\d{3,})\b`)

type rowRead struct {
	s         *store.Store
	d         Deps
	row       store.ReviewRow
	owner     string
	ownerGone bool
}

type waitClock struct {
	fact  string
	after time.Duration
	on    func(rowRead) bool
	line  func(rowRead) string
}

var waitClocks = []waitClock{
	{"wait-grade", 30 * time.Minute, onlyAGrade, gradeWaitLine},
	{"wait-lane", time.Hour, noLiveLane, laneWaitLine},
	{"wait-red", time.Hour, redFromElsewhere, redWaitLine},
}

func alarmRowWaits(s *store.Store, d Deps, now time.Time) {
	owners := map[int]string{}
	for lane, prs := range must(s.Owners()) {
		for _, number := range prs {
			owners[number] = lane
		}
	}
	rows := must(s.ReviewRows(d.Repo))
	gone := map[string]bool{}
	for _, row := range rows {
		owner := or(owners[row.Number], row.Lane)
		if _, read := gone[owner]; !read && owner != "" {
			gone[owner] = must(Running(s, d.Drive, owner)) == Root
		}
	}
	for _, clock := range waitClocks {
		since := must(s.FactsOfKind(d.Repo, clock.fact))
		for _, row := range rows {
			owner := or(owners[row.Number], row.Lane)
			read := rowRead{s, d, row, owner, gone[owner]}
			if !clock.on(read) {
				continue
			}
			first, seen := since[store.FactKey{Number: row.Number, Head: row.Head}]
			if !seen {
				must(s.PutFact(row.Repo, row.Number, row.Head, clock.fact, "waiting", now))
				continue
			}
			if now.Sub(first) < clock.after || isForeign(read) {
				continue
			}
			subject := fmt.Sprintf("%s#%d at %s", row.Repo, row.Number, row.Head[:min(headShown, len(row.Head))])
			if must(s.Escalate(subject, clock.fact, Root, now)) {
				do(d.Post(Post{Kind: "blocker", To: []string{Root}, PRs: []int{row.Number}, Text: fmt.Sprintf("%s: for %d min the owner has been waiting %s. %s", subject, int(now.Sub(first).Minutes()), clock.line(read), row.Title)}))
			}
		}
	}
}

func isForeign(read rowRead) bool {
	return !read.row.Tapped && must(read.s.Facts(read.row.Repo, read.row.Number, read.row.Head))[prrow.FactForeign] != ""
}

func onlyAGrade(read rowRead) bool {
	return strings.HasPrefix(read.row.WaitsOn, prrow.NotGraded)
}

func gradeWaitLine(read rowRead) string {
	facts := must(read.s.Facts(read.row.Repo, read.row.Number, read.row.Head))
	var owed []string
	if facts["finder"] == "" {
		owed = append(owed, must(Holder(read.s, read.d.Drive, finderDesk)))
	}
	if facts["fidelity"] == "" {
		owed = append(owed, must(Holder(read.s, read.d.Drive, fidelityDesk)))
	}
	return fmt.Sprintf("on a grade alone, with every other gate clear; it is owed by %s", strings.Join(owed, " and "))
}

func noLiveLane(read rowRead) bool {
	row := read.row
	open := row.State == prrow.StateOpen || row.State == prrow.StateTapped
	return open && read.ownerGone && row.Hold == prrow.None && !row.ReadyForTap && row.ParentState != prrow.ParentOpen && row.SupersededBy == prrow.None
}

func laneWaitLine(read rowRead) string {
	return fmt.Sprintf("on a lane to own it: %s is gone and it waits on %s; root owes it an owner with `%s`", read.owner, read.row.WaitsOn, Request{Verb: "owner", Lane: "<lane>", PRs: []int{read.row.Number}}.Command())
}

func redFromElsewhere(read rowRead) bool {
	return strings.HasPrefix(read.row.WaitsOn, prrow.RedAtHead) && redClassOf(read).Class == prrow.RedInherited
}

func redWaitLine(read rowRead) string {
	class := redClassOf(read)
	owner := or(class.Owner, read.owner)
	for _, match := range namedPR.FindAllStringSubmatch(class.Action, -1) {
		if match[1] != fmt.Sprint(read.row.Number) {
			return fmt.Sprintf("on a red its own change did not break: %s broke it, and %s owes the fix or the wait", match[0], owner)
		}
	}
	return fmt.Sprintf("on a red its own change did not break, and no record names the PR that did; %s owes that name", owner)
}

func redClassOf(read rowRead) prrow.RedClass {
	facts := must(read.s.Facts(read.row.Repo, read.row.Number, shortRedHead(read.row.Head)))
	class, _ := prrow.ParseRedClass(facts[redClassFact])
	return class
}

func shortRedHead(head string) string {
	return head[:min(prrow.ShortestRedHead, len(head))]
}

func foldRedClass(s *store.Store, r Record, repo string, at time.Time) {
	class, ok := prrow.ParseRedClass(r.Text)
	if !ok {
		return
	}
	for _, number := range r.PRs() {
		must(s.PutFact(repo, number, shortRedHead(class.Head), redClassFact, r.Text, at))
	}
}
