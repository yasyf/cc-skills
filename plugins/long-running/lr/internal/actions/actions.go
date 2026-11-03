package actions

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"strings"

	_ "modernc.org/sqlite"
)

const fileSuffix = ".sqlite3"

type Action struct {
	Key                 string         `json:"-"`
	IncidentID          string         `json:"incident_id"`
	ActionID            string         `json:"action_id"`
	Kind                string         `json:"kind"`
	Target              string         `json:"target"`
	AuthorityRef        string         `json:"authority_ref"`
	InputRevision       int64          `json:"input_revision"`
	OwnerGeneration     int64          `json:"owner_generation"`
	AcceptedAt          string         `json:"accepted_at"`
	DispatchID          string         `json:"dispatch_id"`
	StartedAt           string         `json:"started_at"`
	Deadline            string         `json:"deadline"`
	Status              string         `json:"status"`
	Response            map[string]any `json:"response"`
	VerificationReceipt map[string]any `json:"verification_receipt"`
	Reason              string         `json:"reason"`
}

type Incident struct {
	ID              string
	Owner           string
	OwnerGeneration int64
	OpenedAt        string
	InputRevision   int64
	Status          string
	PendingOwner    string
	Facts           map[string]any
	Actions         []Action
	Decisions       []map[string]any
	Milestones      []map[string]any
}

type Store interface {
	IDs() ([]string, error)
	Load(id string) (Incident, error)
}

type Files struct {
	Root string
}

func (f Files) root() (string, error) {
	if f.Root != "" {
		return f.Root, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".claude", "long-running", "incidents"), nil
}

func (f Files) IDs() ([]string, error) {
	root, err := f.root()
	if err != nil {
		return nil, err
	}
	paths, err := filepath.Glob(filepath.Join(root, "*"+fileSuffix))
	if err != nil {
		return nil, err
	}
	ids := make([]string, len(paths))
	for i, path := range paths {
		ids[i] = strings.TrimSuffix(filepath.Base(path), fileSuffix)
	}
	slices.Sort(ids)
	return ids, nil
}

func (f Files) Load(id string) (Incident, error) {
	if id == "" || strings.ContainsAny(id, "/\x00") {
		return Incident{}, fmt.Errorf("incident ID %q must be a nonempty filename component", id)
	}
	root, err := f.root()
	if err != nil {
		return Incident{}, err
	}
	path := filepath.Join(root, id+fileSuffix)
	if _, err := os.Stat(path); err != nil {
		return Incident{}, err
	}
	source := url.URL{Scheme: "file", Path: path, RawQuery: "mode=ro&_pragma=busy_timeout(5000)"}
	db, err := sql.Open("sqlite", source.String())
	if err != nil {
		return Incident{}, err
	}
	defer db.Close()
	snapshot, err := db.Begin()
	if err != nil {
		return Incident{}, err
	}
	defer snapshot.Rollback()
	return read(snapshot)
}

func read(snapshot *sql.Tx) (Incident, error) {
	var incident Incident
	var pending sql.NullString
	if err := snapshot.QueryRow("SELECT incident_id, owner, owner_generation, opened_at, input_revision, status, pending_owner FROM incident WHERE singleton = 1").Scan(
		&incident.ID, &incident.Owner, &incident.OwnerGeneration, &incident.OpenedAt, &incident.InputRevision, &incident.Status, &pending,
	); err != nil {
		return Incident{}, err
	}
	incident.PendingOwner = pending.String
	var err error
	if incident.Decisions, err = metadata(snapshot, "decisions"); err != nil {
		return Incident{}, err
	}
	if incident.Milestones, err = metadata(snapshot, "milestones"); err != nil {
		return Incident{}, err
	}
	if incident.Facts, err = facts(snapshot); err != nil {
		return Incident{}, err
	}
	if incident.Actions, err = records(snapshot); err != nil {
		return Incident{}, err
	}
	return incident, nil
}

func metadata(snapshot *sql.Tx, name string) ([]map[string]any, error) {
	var value string
	if err := snapshot.QueryRow("SELECT value FROM metadata WHERE name = ?", name).Scan(&value); err != nil {
		return nil, err
	}
	var entries []map[string]any
	err := json.Unmarshal([]byte(value), &entries)
	return entries, err
}

type factRow struct {
	path, parent, key, kind string
	record                  sql.NullString
}

func facts(snapshot *sql.Tx) (map[string]any, error) {
	rows, err := snapshot.Query("SELECT path, parent, key, kind, record FROM fact")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var found []factRow
	for rows.Next() {
		var row factRow
		if err := rows.Scan(&row.path, &row.parent, &row.key, &row.kind, &row.record); err != nil {
			return nil, err
		}
		found = append(found, row)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	nodes := map[string]map[string]any{"": {}}
	for _, row := range found {
		if row.kind == "dict" {
			nodes[row.path] = map[string]any{}
		}
	}
	for _, row := range found {
		if row.kind == "dict" {
			nodes[row.parent][row.key] = nodes[row.path]
			continue
		}
		var value any
		if err := json.Unmarshal([]byte(row.record.String), &value); err != nil {
			return nil, fmt.Errorf("fact %s: %w", row.path, err)
		}
		nodes[row.parent][row.key] = value
	}
	return nodes[""], nil
}

func records(snapshot *sql.Tx) ([]Action, error) {
	rows, err := snapshot.Query("SELECT action_key, record FROM action ORDER BY ordinal")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var found []Action
	for rows.Next() {
		var key, record string
		if err := rows.Scan(&key, &record); err != nil {
			return nil, err
		}
		var action Action
		if err := json.Unmarshal([]byte(record), &action); err != nil {
			return nil, fmt.Errorf("action %s: %w", key, err)
		}
		action.Key = key
		found = append(found, action)
	}
	return found, rows.Err()
}
