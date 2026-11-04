package orca

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
)

const (
	defaultLaneModel  = "opus"
	defaultLaneEffort = "xhigh"
	placeLocal        = "local"
	placeRemote       = "remote"
	workerAttempt     = "brief.worker"
)

var ownerRow = regexp.MustCompile(`\bm[0-9]{1,3}\b`)

type NewBrief struct {
	Title, BodyFile, Contract, Scale, Place string
	Topics                                  []string
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
		return "", errors.New("--body-file needs --title")
	}
	body, err := os.ReadFile(n.BodyFile)
	if err != nil {
		return "", fmt.Errorf("--body-file: %w", err)
	}
	said := strings.TrimSpace(string(body))
	if said == "" {
		return "", fmt.Errorf("--body-file: %s is empty", n.BodyFile)
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
	if rows := ownerRow.FindAllString(said, -1); len(rows) > 0 {
		slices.Sort(rows)
		sections = append(sections, "Owner orders: owner:"+strings.Join(slices.Compact(rows), " owner:"))
	}
	return strings.Join(sections, "\n\n") + "\n", nil
}

func (n NewBrief) ccxLine() (string, error) {
	switch n.Place {
	case placeLocal:
		return "ccx: role=build incident=none place=local why=mac-inputs", nil
	case placeRemote:
		return "ccx: role=build incident=none", nil
	}
	return "", fmt.Errorf("--place %s: choose %s or %s", n.Place, placeLocal, placeRemote)
}
