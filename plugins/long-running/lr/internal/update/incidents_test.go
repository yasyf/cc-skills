package update

import (
	"reflect"
	"strings"
	"testing"
)

func opened(seq int64, lane, text string, minutes int, to string) Record {
	r := record(seq, lane, kindIncident, text, minutes)
	r.Status, r.To = statusOpen, []string{to}
	return r
}

func incidentFixture() ([]Lane, []Record) {
	lanes := []Lane{heard("api-fix", "desired", 5), heard("creds-fix", "desired", 500)}
	records := []Record{
		opened(1, "root", "INCIDENT OPEN api: 5xx on runs", 90, "api-fix"),
		opened(2, "root", "INCIDENT OPEN creds: rotation failing", 80, "creds-fix"),
		opened(3, "root", "INCIDENT OPEN sso: login loop", 70, "sso-fix"),
		opened(4, "api-fix", "SCOPE api-fix: active yes; plan keeps path yes", 60, root),
		record(5, "api-fix", "mechanism", "pool exhausted by retries", 50),
		opened(6, "api-fix", "SCOPE api-fix: active yes; 3 teams hit", 40, root),
		record(7, "api-fix", "report", "FIX-LIVE api: retry cap live on prod", 20),
		record(8, "root", "decide", "INCIDENT sso CLOSED", 10),
	}
	records[7].To = []string{"sso-fix"}
	return lanes, records
}

func TestAnIncidentReadsItsNewestScopeMechanismAndFixLiveAndOnlyARegistryLiveLaneIsOnIt(t *testing.T) {
	lanes, records := incidentFixture()
	byName := map[string]Incident{}
	for _, i := range viewOf(lanes, records, nil).incidents(records, testDrive) {
		byName[i.Name] = i
	}
	api := byName["api"]
	if api.Scope.Seq != 6 || api.Mechanism.Seq != 5 || api.FixLive.Seq != 7 || api.work.who != "api-fix" {
		t.Fatalf("api = scope #%d mechanism #%d fix-live #%d on %q", api.Scope.Seq, api.Mechanism.Seq, api.FixLive.Seq, api.work.who)
	}
	if creds := byName["creds"]; creds.work.who != nobody || !creds.Open {
		t.Fatalf("creds-fix is quiet past the registry window, yet creds is on %q, open %v", creds.work.who, creds.Open)
	}
	if want := "closes it: the coordinator posts INCIDENT api CLOSED citing #7"; api.lines()[7] != want {
		t.Fatalf("closes line = %q", api.lines()[7])
	}
}

func TestTheIncidentTableListsUnownedFirstAndAgreesWithTheUpdatesIncidentRows(t *testing.T) {
	lanes, records := incidentFixture()
	v := viewOf(lanes, records, nil)
	table := incidentTable(v.incidents(records, testDrive))
	names := make([]string, 0, len(table)-2)
	for _, line := range table[2:] {
		names = append(names, strings.TrimSpace(strings.Split(line, "|")[1]))
	}
	if want := []string{"creds", "api", "sso"}; !reflect.DeepEqual(names, want) {
		t.Fatalf("table order = %v, want %v", names, want)
	}
	open := map[string]bool{}
	for _, row := range v.incidentRows(records, testDrive) {
		open[row.Name] = !row.Done
	}
	for _, i := range v.incidents(records, testDrive) {
		if open[i.Name] != i.Open {
			t.Fatalf("lr update calls %s open=%v, the table open=%v", i.Name, open[i.Name], i.Open)
		}
	}
}

func TestARowCallsAnIncidentFixedOnlyWithAFixLiveSeq(t *testing.T) {
	lanes, records := incidentFixture()
	closing := record(9, "root", "decide", "INCIDENT api CLOSED", 5)
	closing.To = []string{"api-fix"}
	records = append(records, closing)
	for _, i := range viewOf(lanes, records, nil).incidents(records, testDrive) {
		switch i.Name {
		case "api":
			if want := "fixed: fix live by #7, closed by #9"; i.state() != want {
				t.Fatalf("api state = %q, want %q", i.state(), want)
			}
		case "sso":
			if strings.Contains(i.state(), "fixed") || !strings.Contains(i.state(), "no fix-live record") {
				t.Fatalf("sso closed with no FIX-LIVE reads %q", i.state())
			}
		}
	}
}

func TestAStandingLanesIncidentRecordWithNoSlugMakesNoRow(t *testing.T) {
	lanes, records := incidentFixture()
	records = append(records,
		opened(9, "supervisor", "PAGE https://example.test/p1 | a drive page", 5, root),
		opened(10, "owner-relay", "7:03 PM OWNER in chat about freezes", 4, root),
		opened(11, "root", "INCIDENT dd-42 monitor 42", 3, "owner-relay"),
	)
	found := viewOf(lanes, records, nil).incidents(records, testDrive)
	names := make([]string, 0, len(found))
	for _, i := range found {
		names = append(names, i.Name)
	}
	if want := []string{"api", "creds", "sso", "dd-42"}; !reflect.DeepEqual(names, want) {
		t.Fatalf("rows = %v, want %v", names, want)
	}
}

func TestAnIncidentResolvedWithNoClosingRecordNamesNoRecord(t *testing.T) {
	if got, want := (Incident{}).state(), "closed with no fix-live record"; got != want {
		t.Fatalf("state = %q, want %q", got, want)
	}
	if got, want := (Incident{FixLive: Record{Seq: 7}}).state(), "fixed: fix live by #7, closed"; got != want {
		t.Fatalf("state = %q, want %q", got, want)
	}
}

func TestAFixLiveMentionInsideAnotherRecordIsNoMilestone(t *testing.T) {
	if got := milestone(record(1, "api-fix", "report", "stays open until FIX-LIVE", 1)); got != "" {
		t.Fatalf("milestone = %q", got)
	}
	if got := milestone(record(1, "api-fix", "report", "5:12 PM FIX-LIVE api: live", 1)); got != milestoneFixLive {
		t.Fatalf("milestone = %q", got)
	}
}

func TestAnIncidentIsDescribedByItsOwnRecordsTheCoordinatorsWordsFirst(t *testing.T) {
	topic := func(r Record, name string) Record {
		r.Topic = name
		return r
	}
	launch := topic(record(1, root, "go", "orca-desk: launch pool-fix-2 NOW incident xhigh brief=/x/brief.md", 90), "pool-fix-2")
	goLine := topic(record(2, root, "go", "GO fix lane pool-fix for class pool: retries exhaust the pool; cap them", 80), "pool")
	reported := topic(record(3, root, "report", "Runs are starving because retries exhaust the pool. pool-fix has the go.", 70), "pool")
	mechanism := record(4, "pool-fix", "mechanism", "MECHANISM pool: each retry opens a second connection", 60)
	scope := opened(5, "pool-fix", "SCOPE pool-fix: active yes; 3 teams hit", 50, root)
	other := topic(record(6, root, "report", "Another incident entirely.", 40), "sso")
	for _, tt := range []struct {
		name    string
		records []Record
		want    string
	}{
		{"its INCIDENT line", []Record{opened(7, root, "INCIDENT OPEN pool: 5xx on runs", 90, "pool-fix"), reported}, "5xx on runs"},
		{"a report beats a go", []Record{launch, goLine, reported, mechanism}, "Runs are starving because retries exhaust the pool"},
		{"a go line loses its lead", []Record{launch, goLine, mechanism}, "retries exhaust the pool; cap them"},
		{"its fix lane's mechanism when the coordinator only launched it", []Record{launch, scope, mechanism}, "each retry opens a second connection"},
		{"nothing when only a launch and a scope name it", []Record{launch, scope, other}, ""},
	} {
		if got := describeIncident(tt.records, "pool"); got != tt.want {
			t.Errorf("%s: got %q, want %q", tt.name, got, tt.want)
		}
	}
}

func TestAQuietLaneHoldingAnOpenPROwnsItsIncidentAndARetiredOrEmptyHandedOneDoesNot(t *testing.T) {
	parked := heard("drain-repair-1", "desired", 500)
	parked.PRs = []int{50026}
	retired := heard("drain-fix", "retired", 500)
	retired.PRs = []int{50025}
	records := []Record{opened(1, "root", "INCIDENT OPEN drain: runs crash on node roll", 600, "drain-fix")}

	found := viewOf([]Lane{parked, retired}, records, nil).incidents(records, testDrive)

	if len(found) != 1 || found[0].unowned() || !strings.HasPrefix(found[0].work.who, "drain-repair-1, quiet since ") || !strings.HasSuffix(found[0].work.who, ", holding #50026") {
		t.Fatalf("incidents = %+v", found)
	}
	if strings.Contains(found[0].work.who, "drain-fix") {
		t.Fatalf("a retired lane reads as on it: %q", found[0].work.who)
	}

	empty := viewOf([]Lane{heard("drain-repair-1", "desired", 500)}, records, nil).incidents(records, testDrive)
	if len(empty) != 1 || !empty[0].unowned() {
		t.Fatalf("a quiet lane holding nothing owns its incident: %+v", empty)
	}
}
