package asks

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

func TestALaneWhoseNewestRecordIsAHandoffStillOwnsAnAskAndADoneLaneDoesNot(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	for _, draft := range []client.Draft{
		{Drive: "d1", Lane: "rotated-1", Kind: "handoff", Text: "handoff before compacting; keeps working"},
		{Drive: "d1", Lane: "finished-1", Kind: "done", Text: "done"},
	} {
		if _, err := client.Post(ctx, draft); err != nil {
			t.Fatal(err)
		}
	}
	refuse := refusal(time.Now().Add(time.Minute), "d1")

	if why, err := refuse(ctx, "rotated-1"); err != nil || why != "" {
		t.Fatalf("handoff lane refused %q, %v; want it live", why, err)
	}
	if why, err := refuse(ctx, "finished-1"); err != nil || !strings.Contains(why, "since its done") {
		t.Fatalf("done lane refused %q, %v; want it ended", why, err)
	}
}
