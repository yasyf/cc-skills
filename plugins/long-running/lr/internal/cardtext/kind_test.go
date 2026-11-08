package cardtext

import (
	"errors"
	"strings"
	"testing"
)

func TestACardNamingAPRSaysWhatKindOfChangeItIsAndAPortSaysWhatTheNewTreeForces(t *testing.T) {
	port := func([]int) (bool, error) { return true, nil }
	notPort := func([]int) (bool, error) { return false, nil }
	for name, tc := range map[string]struct {
		title, body string
		asks        bool
		ports       func([]int) (bool, error)
		refused     string
	}{
		"a pick about a port with no label":           {"Carry both helpers to the browser for #51734?", "The old server had them in scope.", true, port, "is about a port and does not say so"},
		"a pick about a port that says so":            {"Port #51734: carry both helpers to the browser?", "The new tree mounts interfaces in the browser, where neither helper exists.", true, port, ""},
		"a port pick that skips what the tree forces": {"Port #51734: carry both helpers to the browser?", "It is ready otherwise.", true, port, "what differs in the new tree"},
		"a port notice that asks nothing":             {"Port #51734 landed", "It is live.", false, port, ""},
		"a PR with no kind":                           {"Land #52089 tonight?", "It stops a dropped reply.", true, notPort, "not what kind of change it is"},
		"a fix that says so":                          {"Land the relay fix #52089 tonight?", "It stops a dropped reply.", true, notPort, ""},
		"a card naming no PR":                         {"Keep the window open?", "Storage fills in a week.", true, port, ""},
		"a number only in a hidden cite":              {"Keep the window open?", "Storage fills in a week. <!-- cci #471653 -->", true, port, ""},
	} {
		t.Run(name, func(t *testing.T) {
			err := Unlabeled(tc.title, tc.body, tc.asks, tc.ports)
			if tc.refused == "" && err != nil || tc.refused != "" && (err == nil || !strings.Contains(err.Error(), tc.refused)) {
				t.Fatalf("Unlabeled = %v, want refusal %q", err, tc.refused)
			}
		})
	}
	if err := Unlabeled("Land #52089?", "", true, func([]int) (bool, error) { return false, errors.New("locked") }); err == nil || !strings.Contains(err.Error(), "locked") {
		t.Fatalf("Unlabeled = %v, want the ledger's failure", err)
	}
}
