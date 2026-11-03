package handoff

import (
	"fmt"
	"strings"
	"testing"
)

func TestARecordOverTheCapNamesItsLargestSectionAndPart(t *testing.T) {
	record := "# r\n\n## Open tasks\n- #1 x\n\n## Open owner asks\n\n### small\n" + strings.Repeat("s", 100) + "\n\n### huge\n" + strings.Repeat("h", progressCap) + "\n"

	refusal := oversized(record)

	head := fmt.Sprintf("progress record is %d bytes, over the %d-byte cap: its largest section is `Open owner asks`", len(record), progressCap)
	if !strings.HasPrefix(refusal, head) {
		t.Fatalf("refusal %q", refusal)
	}
	if part := fmt.Sprintf("most of it `huge` at %d bytes", progressCap+len("### huge\n")); !strings.Contains(refusal, part) {
		t.Fatalf("refusal %q does not name %q", refusal, part)
	}
	if got := oversized(record[:progressCap]); got != "" {
		t.Fatalf("a record at the cap was refused: %q", got)
	}
}

func TestHeadingsInsideAFenceNeverSplitASection(t *testing.T) {
	record := "## Inboxes\n\n```md\n## Example heading\n```\n" + strings.Repeat("i", progressCap) + "\n\n## Lint findings\n- none\n"

	if refusal := oversized(record); !strings.Contains(refusal, "its largest section is `Inboxes`") {
		t.Fatalf("refusal %q", refusal)
	}
}

func TestAnOversizedRecordWithoutSectionsStillNamesItsText(t *testing.T) {
	if refusal := oversized(strings.Repeat("x", progressCap+1)); !strings.Contains(refusal, "`text before the first section`") {
		t.Fatalf("refusal %q", refusal)
	}
}
