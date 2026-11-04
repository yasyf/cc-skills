package landing

import (
	"testing"

	"github.com/yasyf/cc-inbox/client"
)

func TestOnlyTheOwnersOwnWordNamingThePROrdersIt(t *testing.T) {
	for _, tt := range []struct {
		name, lane, text string
		want             bool
	}{
		{"a relayed thread answer", "owner-relay", "Owner in thread: merge #7", true},
		{"a later relay", "owner-relay-2", "Owner in thread: merge #7", true},
		{"a board tap", "board-watch", "Owner on board: Land #7 now", true},
		{"a lane's own word", "root", "Enqueue #7 now", false},
		{"his word on another PR", "owner-relay", "Owner in thread: merge #70", false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			if got := holdsOwnerWord(client.Record{Lane: tt.lane, Text: tt.text}, 7); got != tt.want {
				t.Fatalf("got %v, want %v", got, tt.want)
			}
		})
	}
}
