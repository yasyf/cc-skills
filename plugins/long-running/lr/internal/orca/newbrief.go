package orca

import (
	"cmp"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
)

const (
	defaultLaneModel  = "opus"
	defaultLaneEffort = "high"
	placeLocal        = "local"
	placeRemote       = "remote"
	workerAttempt     = "brief.worker"
	standingFile      = "brief-lines.txt"
	bodyFromStdin     = "-"
)

var (
	ccxPairGlue   = regexp.MustCompile(`,\s*([a-z-]+=)`)
	assignedOrder = regexp.MustCompile(`^owner:m\d+(?:\.\d+)?$`)
)

type NewBrief struct {
	Title, BodyFile, Contract, Scale, Place, Why, CCX, OwnerPick, Step string
	Topics, Orders                                                     []string
	StandingDir                                                        string
	Stdin                                                              io.Reader
}

func (n NewBrief) Write(stateDir, lane string) (string, error) {
	dir := filepath.Join(stateDir, lane)
	if _, err := os.Stat(filepath.Join(dir, workerAttempt)); !errors.Is(err, fs.ErrNotExist) {
		if err != nil {
			return "", err
		}
		return "", fmt.Errorf("lane %s already started from its brief, and a brief gets one attempt; launch the next number", lane)
	}
	text, err := n.text(lane)
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return "", err
	}
	path := filepath.Join(dir, briefFile)
	return path, os.WriteFile(path, []byte(text), 0o600)
}

func (n NewBrief) text(lane string) (string, error) {
	if n.Title == "" {
		return "", errors.New("--body needs --title")
	}
	body, err := n.body()
	if err != nil {
		return "", fmt.Errorf("--body: %w", err)
	}
	said := strings.TrimSpace(string(body))
	if said == "" {
		return "", fmt.Errorf("--body: %s is empty", n.BodyFile)
	}
	ccxLine, err := n.ccxLine()
	if err != nil {
		return "", err
	}
	header := "Scale: " + n.Scale + "\n" + ccxLine
	switch {
	case !briefSlice.MatchString(said):
		header = sliceHeader(lane, cmp.Or(n.Step, stepAll), n.Topics, said) + "\n" + header
	case n.Step != "" || len(n.Topics) > 0:
		return "", fmt.Errorf("the body carries its own `%s` line, and --step and --topics write that line; drop them or the body's line. A body that is another lane's brief relaunches that lane instead: `lr desks start` for a standing desk, or `lr launch lane <stem> <model> <effort> <its brief>`, which copies it under the next name", sliceLine)
	}
	sections := []string{fmt.Sprintf("# %s: %s", lane, n.Title), header}
	if n.Contract != "" {
		contract, err := os.ReadFile(n.Contract)
		if err != nil {
			return "", fmt.Errorf("--contract: %w", err)
		}
		sections = append(sections, strings.TrimSpace(string(contract)))
	}
	sections = append(sections, said)
	standing, err := os.ReadFile(filepath.Join(n.StandingDir, standingFile))
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		return "", fmt.Errorf("standing lines: %w", err)
	}
	if lines := strings.TrimSpace(string(standing)); lines != "" {
		sections = append(sections, lines)
	}
	if lanes.BriefAssignedOrders.MatchString(strings.Join(sections, "\n")) {
		return "", fmt.Errorf("the body, contract or standing lines carry an %s line; name each order the lane delivers with --order owner:m<row> instead", lanes.AssignedOrdersLine)
	}
	if len(n.Orders) > 0 {
		for _, order := range n.Orders {
			if !assignedOrder.MatchString(order) {
				return "", fmt.Errorf("--order takes owner:m<row>, an owner row this lane delivers, not %q", order)
			}
		}
		sections = append(sections, lanes.AssignedOrdersLine+" "+strings.Join(n.Orders, " "))
	}
	if n.OwnerPick != "" {
		if !ownerPick.MatchString(n.OwnerPick) {
			return "", fmt.Errorf("--owner-pick takes owner:m<row>, the owner row whose words asked for this model or effort, not %q", n.OwnerPick)
		}
		sections = append(sections, lanes.OwnerPickLine+" "+n.OwnerPick)
	}
	return strings.Join(sections, "\n\n") + "\n", nil
}

func (n NewBrief) body() ([]byte, error) {
	if n.BodyFile == bodyFromStdin {
		return io.ReadAll(n.Stdin)
	}
	return os.ReadFile(n.BodyFile)
}

func (n NewBrief) ccxLine() (string, error) {
	if n.CCX != "" {
		line := "ccx: " + ccxPairGlue.ReplaceAllString(n.CCX, " $1")
		if !briefIncident.MatchString(line) {
			line += " incident=none"
		}
		return line, nil
	}
	switch {
	case n.Place == placeRemote && n.Why == "":
		return "ccx: role=build incident=none", nil
	case n.Place == placeRemote:
		return "", fmt.Errorf("--why %s keeps a lane on this Mac, so it takes --place %s", n.Why, placeLocal)
	case n.Place == placeLocal && slices.Contains(lanes.MacNeeds, n.Why):
		return "ccx: role=build incident=none place=local why=" + n.Why, nil
	case n.Place == placeLocal:
		return "", fmt.Errorf("--place %s takes --why naming a need only this Mac meets: %s", placeLocal, lanes.MacNeedsLine)
	}
	return "", fmt.Errorf("--place %s: choose %s or %s", n.Place, placeRemote, placeLocal)
}
