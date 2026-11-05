package gates

import (
	"slices"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestARowAnswersOnlyForItsOwnHeadAndUnknownIsNotReady(t *testing.T) {
	for _, c := range []struct {
		name  string
		row   store.ReviewRow
		found bool
		wait  string
		read  string
	}{
		{"no row", store.ReviewRow{}, false, NoRow, NoRow},
		{"an older head", store.ReviewRow{Head: oldHead, ReadyForTap: true}, true, "its review row is at aa11bb2, not e718a74", "its review row is at aa11bb2, not e718a74"},
		{"ready", store.ReviewRow{Head: newHead, ReadyForTap: true}, true, "", ""},
		{"waiting", store.ReviewRow{Head: newHead, WaitsOn: "not graded"}, true, "not graded", ""},
		{"not ready with no words", store.ReviewRow{Head: newHead}, true, notReadyYet, ""},
		{"a short head names the same commit", store.ReviewRow{Head: newHead[:7], ReadyForTap: true}, true, "", ""},
		{"no head yet", store.ReviewRow{ReadyForTap: true}, true, "its review row is at , not e718a74", "its review row is at , not e718a74"},
	} {
		t.Run(c.name, func(t *testing.T) {
			if got := RowWait(c.row, c.found, newHead); got != c.wait {
				t.Errorf("RowWait = %q, want %q", got, c.wait)
			}
			if got := RowRead(c.row, c.found, newHead); got != c.read {
				t.Errorf("RowRead = %q, want %q", got, c.read)
			}
		})
	}
}

func TestATapIsReadAtTheHeadItNames(t *testing.T) {
	for _, c := range []struct {
		name, named, head, refusal string
	}{
		{"no head is the row's head", "", newHead, ""},
		{"a prefix is the row's head", newHead[:7], newHead, ""},
		{"its full head", newHead, newHead, ""},
		{"an older head is refused", oldHead, oldHead, "its review row is at e718a74, not aa11bb2"},
	} {
		t.Run(c.name, func(t *testing.T) {
			s, err := store.Memory()
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = s.Close() })
			if err := s.PutReviewRows([]store.ReviewRow{{Repo: repo, Number: 1, Head: newHead, ReadyForTap: true}}); err != nil {
				t.Fatal(err)
			}
			head, refusal, err := TapAt(s, repo, bus.Tap{PR: 1, Head: c.named})
			if err != nil || head != c.head || refusal != c.refusal {
				t.Fatalf("TapAt = %q, %q, %v", head, refusal, err)
			}
		})
	}
}

func TestAMissingRowSaysWhenTheBoxHoldsNoLandingStore(t *testing.T) {
	for _, c := range []struct {
		name string
		rows []store.ReviewRow
		want string
	}{
		{"no rows at all", nil, NoStore},
		{"another repo's rows", []store.ReviewRow{{Repo: "o/other", Number: 1, Head: newHead}}, NoStore},
		{"another PR's row", []store.ReviewRow{{Repo: repo, Number: 2, Head: newHead}}, NoRow},
	} {
		t.Run(c.name, func(t *testing.T) {
			s, err := store.Memory()
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = s.Close() })
			if err := s.PutReviewRows(c.rows); err != nil {
				t.Fatal(err)
			}
			if _, refusal, err := TapAt(s, repo, bus.Tap{PR: 1, Head: newHead}); err != nil || refusal != c.want {
				t.Errorf("TapAt refusal = %q, %v, want %q", refusal, err, c.want)
			}
			if unread, err := RowReadAt(s, repo, 1, newHead); err != nil || unread != c.want {
				t.Errorf("RowReadAt = %q, %v, want %q", unread, err, c.want)
			}
		})
	}
}

func TestMissingAddsTheRowsWaitOnlyWhenNoOtherGateSaysWhy(t *testing.T) {
	tapped := green
	tapped.Tap = machine.TapTapped
	for _, c := range []struct {
		name   string
		change func(*machine.Facts)
		want   []string
	}{
		{"clear", func(*machine.Facts) {}, nil},
		{"only the row", func(f *machine.Facts) { f.RowWait = "its PR body has no Summary" }, []string{"its row waits on: its PR body has no Summary"}},
		{"the row and no tap", func(f *machine.Facts) { f.RowWait, f.Tap = "not graded", "" }, []string{NoTap, "its row waits on: not graded"}},
		{"a gate that already says why", func(f *machine.Facts) { f.RowWait, f.CI = "CI is not green", machine.CIPending }, []string{"CI is not green"}},
		{"exempt", func(f *machine.Facts) { f.RowWait, f.Tap, f.Exempt = "its PR body has no Summary", "", true }, nil},
		{"a standing word owes a go, not a tap", func(f *machine.Facts) { f.RowWait, f.Tap, f.StandingWord = "lands on his word", "", OldTreePortClass }, []string{NoGo}},
		{"a standing word under an until-tap hold still owes his tap", func(f *machine.Facts) {
			f.RowWait, f.Tap, f.StandingWord, f.Hold, f.HoldAwaitsTap = "", "", OldTreePortClass, machine.MoverRoot, true
		}, []string{NoTap}},
	} {
		t.Run(c.name, func(t *testing.T) {
			f := tapped
			c.change(&f)
			if got := Missing(f); !slices.Equal(got, c.want) {
				t.Errorf("Missing = %q, want %q", got, c.want)
			}
		})
	}
}
