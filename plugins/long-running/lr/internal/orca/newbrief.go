package orca

import (
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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
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
	ccxPairGlue = regexp.MustCompile(`,\s*([a-z-]+=)`)
)

type NewBrief struct {
	Title, BodyFile, Contract, Scale, Place, CCX, OwnerPick string
	Topics                                                  []string
	StandingDir                                             string
	Stdin                                                   io.Reader
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
	slice := fmt.Sprintf("%s lane=%s step=all", sliceLine, lane)
	if len(n.Topics) > 0 {
		slice += " topics=" + strings.Join(n.Topics, ",")
	}
	sections := []string{
		fmt.Sprintf("# %s: %s", lane, n.Title),
		strings.Join([]string{slice, "Scale: " + n.Scale, ccxLine}, "\n"),
	}
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
	if rows := owner.Orders(said); len(rows) > 0 {
		slices.Sort(rows)
		sections = append(sections, "Owner orders: owner:"+strings.Join(rows, " owner:"))
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
	switch n.Place {
	case placeLocal:
		return "ccx: role=build incident=none place=local why=" + whyMacInputs, nil
	case placeRemote:
		return "ccx: role=build incident=none", nil
	}
	return "", fmt.Errorf("--place %s: choose %s or %s", n.Place, placeLocal, placeRemote)
}
