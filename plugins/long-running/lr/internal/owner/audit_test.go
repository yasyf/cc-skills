package owner

import (
	"reflect"
	"testing"

	"github.com/yasyf/cc-inbox/client"
)

const auditLedger = `| id | time | message | routed | state |
|---|---|---|---|---|
| m1 | Nov 3 4:50 AM | build the phone app for approvals | 362808 | addressed |
| m2 | Nov 3 4:51 AM | redesign the wire format |  | responded |
| m3 | Nov 3 4:52 AM | mute that monitor for two days |  | addressed |
| m4 | Nov 3 4:53 AM | why does it need Touch ID? |  | responded |
| m5 | Nov 3 4:54 AM | shouldn’t you be fixing that watchdog issue? |  | responded |
| m6 | Nov 3 4:55 AM | Owner on board d50e80e8, Which open pull requests may merge now? (merge-prs): 48201 approved at 1ffd915 |  | addressed |
| m7 | Nov 3 4:56 AM | if the regression is in the new build, how are people hitting it |  | no-order |
| m8 | Nov 3 4:57 AM | is this live yet?<br>also fix the stop hook |  | responded |
| m9 | Nov 3 4:58 AM | close it out and never reopen it \| same cause |  | addressed |
`

func ids(rows []Row) []string {
	out := make([]string, 0, len(rows))
	for _, row := range rows {
		out = append(out, row.ID)
	}
	return out
}

func TestEveryOrderNothingDeliversIsListedAndAReplyAloneDeliversNothing(t *testing.T) {
	rows := Parse([]byte(auditLedger))

	got := ids(Undelivered(rows, nil, nil))

	if want := []string{"m1", "m2", "m3", "m5", "m8", "m9"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestALiveLaneWhoseBriefNamesTheOrderDeliversItAndARetiredOneDoesNot(t *testing.T) {
	rows := Parse([]byte(auditLedger))
	lanes := []Lane{{Name: "phone-app-1", Working: true, Orders: []string{"m1"}}, {Name: "wire-1", Orders: []string{"m2"}}}

	got := ids(Undelivered(rows, lanes, nil))

	if want := []string{"m2", "m3", "m5", "m8", "m9"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func standing(rows []Row) []string {
	out := make([]string, 0, len(rows))
	for _, row := range rows {
		out = append(out, row.ID+" "+row.Status+" "+row.Lane)
	}
	return out
}

func TestALandedOrDoneRecordMovesTheOrderItNamesOrItsLaneCarriesToInProgressNeverToDelivered(t *testing.T) {
	rows := Parse([]byte(auditLedger))
	lanes := []Lane{{Name: "wire-1", Orders: []string{"m2"}}, {Name: "mute-1", Orders: []string{"m3"}}}
	installed := client.Record{Seq: 3, Lane: "install-desk-1", Kind: "done", Text: "installed and seen working"}
	installed.Refs.Lanes = []string{"mute-1"}
	proofs := []client.Record{{Seq: 1, Lane: "wire-1", Kind: "landed", Text: "wire-1: merged, not installed"}, installed, {Seq: 4, Lane: "fix-9", Kind: "landed", Text: "owner:m9 closed out for good"}}

	got := standing(Undelivered(rows, lanes, proofs))

	if want := []string{"m1 open ", "m2 in progress wire-1", "m3 in progress mute-1", "m5 open ", "m8 open ", "m9 in progress fix-9"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestOnlyADeliveredAttrDeliversAndAReportNamingTheRowLeavesItInProgress(t *testing.T) {
	rows := Parse([]byte(auditLedger))
	delivered := client.Record{Seq: 1, Lane: "owner-relay", Kind: "report", Text: "he picked B on the options"}
	delivered.Fields.Attrs = map[string]string{"delivered": "owner:m2 owner:m3"}
	carriedBy := client.Record{Seq: 2, Lane: "owner-orders-sweep-a", Kind: "report", Text: "owner:m1 carried by phone-app-1"}

	got := standing(Undelivered(rows, []Lane{{Name: "owner-relay", Orders: []string{"m5"}}}, []client.Record{delivered, carriedBy}))

	if want := []string{"m1 in progress owner-orders-sweep-a", "m5 open owner-relay", "m8 open ", "m9 open "}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestAnOrderWhoseLastLaneLeftAPartOpenIsParkedWithThatHoldAndLane(t *testing.T) {
	rows := Parse([]byte(auditLedger))
	lanes := []Lane{{Name: "phone-app-1", Orders: []string{"m1"}}, {Name: "wire-1", Orders: []string{"m2"}}}
	proofs := []client.Record{
		{Seq: 1, Lane: "phone-app-1", Kind: "landed", Text: "phone-app-1: the approval tool merged, not installed. Left open: the dashboard page it reports to. Retiring."},
		{Seq: 2, Lane: "owner-orders-sweep-a", Kind: "report", Text: "owner:m1 carried by phone-app-1"},
		{Seq: 3, Lane: "wire-1", Kind: "report", Text: "wire-1: the new format is held behind the SandSQL release freeze"},
		{Seq: 4, Lane: "fix-9", Kind: "report", Text: "owner:m9 is built and its gate holds firm"},
	}

	got := Undelivered(rows, lanes, proofs)

	if want := []string{"m1 parked phone-app-1", "m2 parked wire-1", "m3 open ", "m5 open ", "m8 open ", "m9 in progress fix-9"}; !reflect.DeepEqual(standing(got), want) {
		t.Fatalf("got %q, want %q", standing(got), want)
	}
	if got[0].Hold != "Left open: the dashboard page it reports to" || got[1].Hold != "wire-1: the new format is held behind the SandSQL release freeze" {
		t.Fatalf("holds %q, %q", got[0].Hold, got[1].Hold)
	}
	if want := "m1 Nov 3 4:50 AM: build the phone app for approvals [parked: Left open: the dashboard page it reports to; last lane phone-app]"; got[0].Line() != want {
		t.Fatalf("line %q, want %q", got[0].Line(), want)
	}
}

func TestALiveLaneOwnsAParkedOrderAgain(t *testing.T) {
	rows := Parse([]byte(auditLedger))
	proofs := []client.Record{{Seq: 1, Lane: "phone-app-1", Kind: "landed", Text: "phone-app-1 merged, not installed. Left open: the dashboard page."}}

	got := ids(Undelivered(rows, []Lane{{Name: "phone-app-1", Orders: []string{"m1"}}, {Name: "phone-app-2", Working: true, Orders: []string{"m1"}}}, proofs))

	if want := []string{"m2", "m3", "m5", "m8", "m9"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestABoardTapABareQuestionAndANoOrderMarkAreNotOrders(t *testing.T) {
	for _, row := range Parse([]byte(auditLedger)) {
		want := row.ID != "m4" && row.ID != "m6" && row.ID != "m7"
		if row.Order() != want {
			t.Errorf("%s %q: order %v, want %v", row.ID, row.Text, row.Order(), want)
		}
	}
}

func TestParseUnescapesTheMessageCell(t *testing.T) {
	rows := Parse([]byte(auditLedger))

	if rows[7].Text != "is this live yet? also fix the stop hook" || rows[8].Text != "close it out and never reopen it | same cause" || rows[0].At != "Nov 3 4:50 AM" || rows[6].State != noOrder {
		t.Fatalf("rows %+v", rows)
	}
}

func TestOrdersReadsEachOwnerTokenOnce(t *testing.T) {
	got := Orders("carry owner:m85 and owner:m142; owner:m85 again, not m140 or owner ledger m143")

	if want := []string{"m85", "m142"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

const splitLedger = `| id | time | message | routed | state |
|---|---|---|---|---|
| m203.1 | Nov 3 2:00 PM | is the old-tree failure the same bug or another one? | 384100 | open |
| m203.2 | Nov 3 2:00 PM | also recap the last hour's orders with an ETA each, and ship the incident fix | 384101 | responded |
| m203.3 | Nov 3 2:00 PM | also if an old-tree Mac chat started the run, why is it in the new format? |  | open |
| m204 | Nov 3 2:05 PM | why does it need Touch ID? |  | responded |
`

func TestEachAskOfOneMessageMovesOnlyByItsOwnRowsToken(t *testing.T) {
	proofs := []client.Record{{Lane: "recap-1", Kind: "landed", Text: "owner:m203.2 recapped and shipped"}, {Lane: "other-1", Kind: "landed", Text: "owner:m203 named whole"}}

	got := standing(Undelivered(Parse([]byte(splitLedger)), nil, proofs))

	if want := []string{"m203.1 open ", "m203.2 in progress recap-1", "m203.3 open "}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestOrdersReadsAnAsksTokenApartFromItsMessage(t *testing.T) {
	got := Orders("carry owner:m203.2, then owner:m203.")

	if want := []string{"m203.2", "m203"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestARetractedDeliveryPutsTheOrderBackOnTheAudit(t *testing.T) {
	rows := Parse([]byte(auditLedger))
	delivered := client.Record{Seq: 1, Lane: "fix-1", Kind: "report"}
	delivered.Fields.Attrs = map[string]string{deliveredAttr: "owner:m1"}
	if got := ids(Undelivered(rows, nil, []client.Record{delivered})); !reflect.DeepEqual(got, []string{"m2", "m3", "m5", "m8", "m9"}) {
		t.Fatalf("a delivered order stayed on the audit: %v", got)
	}
	retracted := client.Record{Seq: 2, Lane: "fix-1", Kind: "report"}
	retracted.Fields.Attrs = map[string]string{retractedAttr: "owner:m1"}
	if got := ids(Undelivered(rows, nil, []client.Record{delivered, retracted})); !reflect.DeepEqual(got, []string{"m1", "m2", "m3", "m5", "m8", "m9"}) {
		t.Fatalf("a retracted order left the audit: %v", got)
	}
}
