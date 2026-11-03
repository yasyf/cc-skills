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

var carryNotes = map[string]string{
	gates.BySameDiff: "tap carried: the PR's own diff is the same as at the head he tapped",
	gates.ByFidelity: "tap carried: fidelity judged no behavior change since the head he tapped",
}

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
	_, err = client.Post(ctx, client.Draft{
		Drive: drive,
		Lane:  self,
		Kind:  "note",
		Topic: boardWriteTopic,
		ID:    "tap-carry-write:" + board.MergeBlock + ":" + number + ":" + f.Head,
		PRs:   []string{ref},
		Attrs: map[string]string{"op": "carry", "session": session, "block": board.MergeBlock, "row": number, "head": f.Head, "from": f.TapFrom, "proof": f.CarriedBy, "note": carryNotes[f.CarriedBy]},
		Text:  fmt.Sprintf("board write: carry the owner's tap on #%d from %s to %s on block %s", f.Number, f.TapFrom[:min(10, len(f.TapFrom))], f.Head[:min(10, len(f.Head))], board.MergeBlock),
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
