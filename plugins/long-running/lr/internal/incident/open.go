package incident

import (
	"fmt"
	"path/filepath"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	KindPRReview = "pr-review"
	KindAlert    = "alert"
)

type profile struct {
	pipeline      string
	canaryJob     string
	humanReview   bool
	reviewSurface string
}

var profiles = map[string]profile{
	KindPRReview: {pipeline: "pr-review", canaryJob: "Prepare PR evidence", humanReview: true, reviewSurface: "channel"},
	KindAlert:    {reviewSurface: surfaceThread},
}

type Opening struct {
	ID           string
	Kind         string
	Thread       string
	Onset        string
	Reported     string
	Bus          string
	CommsLane    string
	RootLane     string
	Checkout     string
	Pipeline     string
	Target       string
	Alert        string
	CodePath     string
	Runbook      string
	Repo         string
	Trunk        string
	ExpectConfig string
	OrcaRun      string
	OrcaRepo     string
	Common       string
	EntryPoint   string
	RepairPR     int
	Rulings      []string
	Adopted      map[string]string
	Grants       map[string]string
}

func orNull(value string) any {
	if value == "" {
		return nil
	}
	return value
}

func (o Opening) code() string {
	switch {
	case o.CodePath != "":
		return o.CodePath
	case o.Pipeline != "":
		return "the " + o.Pipeline + " pipeline"
	}
	return "the " + o.Target + " service"
}

func (s Store) Open(o Opening, now time.Time) (string, error) {
	shape, known := profiles[o.Kind]
	if !known {
		return "", fmt.Errorf("--kind %q is neither %s nor %s", o.Kind, KindPRReview, KindAlert)
	}
	for name, value := range map[string]string{"--onset": o.Onset, "--reported": o.Reported} {
		if _, err := store.Parse(value); err != nil {
			return "", fmt.Errorf("%s: %w", name, err)
		}
	}
	if o.ID == "" {
		o.ID = now.UTC().Format("01021504")
	}
	if o.Pipeline == "" {
		o.Pipeline = shape.pipeline
	}
	if o.Target == "" {
		o.Target = o.Pipeline
	}
	if o.Target == "" {
		return "", fmt.Errorf("--kind %s names no pipeline; pass --target, the service or stack the alert is on", o.Kind)
	}
	if o.Alert == "" {
		o.Alert = o.Thread
	}
	checkout, err := filepath.Abs(o.Checkout)
	if err != nil {
		return "", err
	}
	common := any(nil)
	if o.Common != "" {
		absolute, err := filepath.Abs(o.Common)
		if err != nil {
			return "", err
		}
		common = absolute
	}
	var repairPR any
	if o.RepairPR != 0 {
		repairPR = o.RepairPR
	}
	rulings := o.Rulings
	if rulings == nil {
		rulings = []string{}
	}
	adopted, grants := o.Adopted, o.Grants
	if adopted == nil {
		adopted = map[string]string{}
	}
	if grants == nil {
		grants = map[string]string{}
	}
	facts := map[string]any{
		"kind":           o.Kind,
		"pipeline":       orNull(o.Pipeline),
		"target":         o.Target,
		"canary_job":     orNull(shape.canaryJob),
		"human_review":   shape.humanReview,
		"review_surface": shape.reviewSurface,
		"repo":           o.Repo,
		"trunk":          o.Trunk,
		"checkout":       checkout,
		"thread":         o.Thread,
		"alert":          o.Alert,
		"code_path":      o.code(),
		"runbook":        orNull(o.Runbook),
		"adopted":        adopted,
		"onset":          o.Onset,
		"reported":       o.Reported,
		"bus":            o.Bus,
		"comms_lane":     o.CommsLane,
		"root_lane":      o.RootLane,
		"grants":         grants,
		"routes":         routes(),
		"expect_config":  orNull(o.ExpectConfig),
		"repair_pr":      repairPR,
		"orca_run":       orNull(o.OrcaRun),
		"orca_repo":      orNull(o.OrcaRepo),
		"common":         common,
		"rulings":        rulings,
		"entry_point":    orNull(o.EntryPoint),
		"sources":        map[string]int{},
	}
	return o.ID, s.Create(Incident{
		Header: Header{ID: o.ID, Owner: sender(o.ID), OwnerGeneration: 1, OpenedAt: store.Stamp(now), InputRevision: 1, Status: StatusOpen},
		Facts:  facts,
	})
}
