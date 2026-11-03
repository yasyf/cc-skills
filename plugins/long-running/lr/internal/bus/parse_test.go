package bus

import (
	"encoding/json"
	"testing"

	"github.com/yasyf/cc-inbox/client"
)

const parseHead = "e718a7434fa01ee9835f992a9a647a825350732c"

func stored(t *testing.T, kind, body string, to ...string) client.Record {
	t.Helper()
	raw, err := json.Marshal(map[string]any{"seq": 1, "at": "2026-10-31T06:00:00Z", "kind": kind, "lane": "lane-7", "drive": "d", "text": "t", "source": "post", "to": to, "body": json.RawMessage(body)})
	if err != nil {
		t.Fatal(err)
	}
	var record client.Record
	if err := json.Unmarshal(raw, &record); err != nil {
		t.Fatal(err)
	}
	return record
}

func TestABodyTheBusWouldHaveRefusedDoesNotParse(t *testing.T) {
	for name, record := range map[string]client.Record{
		"names many PRs":              stored(t, "lr.grade", `{"pr":[1,2],"head":"`+parseHead+`","grader":"finder","verdict":"clear"}`, "supervisor"),
		"addressed past its audience": stored(t, "lr.state", `{"lane":"lane-7"}`, "root"),
		"not in the table":            stored(t, "lr.evidence", `{"text":"x"}`),
		"no body at all":              stored(t, "lr.note", `null`, "supervisor"),
	} {
		t.Run(name, func(t *testing.T) {
			if parsed, err := parse(record); err == nil {
				t.Fatalf("parse = %+v, want a refusal", parsed)
			}
		})
	}
}

func TestAStoredBodyParsesToItsType(t *testing.T) {
	record, err := parse(stored(t, "lr.grade", `{"pr":45821,"head":"`+parseHead+`","grader":"finder","verdict":"clear"}`, "supervisor"))
	if err != nil {
		t.Fatal(err)
	}
	want := Grade{PR: 45821, Head: parseHead, Grader: "finder", Verdict: "clear"}
	if record.Body != Body(want) || record.Kind() != "grade" || record.Seq != 1 {
		t.Fatalf("record = %+v", record)
	}
}
