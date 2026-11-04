package landing

import (
	"context"
	"errors"
	"fmt"
	"strconv"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

const (
	boardWatchLane  = "board-watch"
	boardWriteTopic = "board-write"
	ownerKind       = "owner"
)

func CarryTaps(ctx context.Context, drive string, carried []machine.Facts) error {
	failed := make([]error, 0, len(carried))
	for _, f := range carried {
		failed = append(failed, carryTap(ctx, drive, f))
	}
	return errors.Join(failed...)
}

func carryTap(ctx context.Context, drive string, f machine.Facts) error {
	number := strconv.Itoa(f.Number)
	ref := f.Repo + "#" + number
	session, err := tappedSession(ctx, drive, ref)
	if err != nil {
		return err
	}
	why := gates.CarryWhy(f.CarriedBy)
	_, err = client.Post(ctx, client.Draft{
		Drive: drive,
		Lane:  self,
		Kind:  "note",
		Topic: boardWriteTopic,
		ID:    "tap-carry-write:" + board.MergeBlock + ":" + number + ":" + f.Head,
		PRs:   []string{ref},
		Attrs: map[string]string{"op": "carry", "session": session, "block": board.MergeBlock, "row": number, "head": f.Head, "from": f.TapFrom, "proof": f.CarriedBy, "note": "tap carried: " + why},
		Text:  fmt.Sprintf("TAP-CARRIED #%d from %s to %s by %s: %s", f.Number, f.TapFrom[:min(10, len(f.TapFrom))], f.Head[:min(10, len(f.Head))], f.CarriedBy, why),
	})
	return err
}

func tappedSession(ctx context.Context, drive, ref string) (string, error) {
	records, err := client.Tail(ctx, client.Query{Drive: drive, Kinds: []string{ownerKind}, Lanes: []string{boardWatchLane}, PR: ref})
	if err != nil {
		return "", err
	}
	session := ""
	for _, record := range records {
		if attrs := record.Fields.Attrs; attrs["block"] == board.MergeBlock && attrs["verdict"] == "approved" {
			session = attrs["session"]
		}
	}
	return session, nil
}
