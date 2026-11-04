package cardtext

import "testing"

func TestACardEndingAProcessMustNameItsPidOrScript(t *testing.T) {
	for card, want := range map[string]string{
		`{"title":"May I restart your board's answer recorder?"}`:                       "restart your board's answer recorder",
		`{"title":"Kill the stale watcher?","summary":"It holds a lock."}`:              "Kill the stale watcher",
		`{"title":"May I restart the recorder?","summary":"meta-desk ends pid 74452."}`: "",
		`{"title":"May I restart the recorder?","summary":"It runs board_watch.py."}`:   "",
		`{"title":"Stop the rollout?","summary":"The server is fine."}`:                 "",
		`{"title":"Which server should end up holding the lease?"}`:                     "",
		`{"title":"What should the summary say?"}`:                                      "",
	} {
		if got := EndsAnUnnamedProcess([]byte(card)); got != want {
			t.Errorf("EndsAnUnnamedProcess(%s) = %q, want %q", card, got, want)
		}
	}
}
