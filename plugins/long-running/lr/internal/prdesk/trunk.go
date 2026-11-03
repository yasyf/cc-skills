package prdesk

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	TrunkAPI    = "https://api.trunk.io/v1/getSubmittedPullRequest"
	trunkDrive  = "trunk"
	trunkSource = "trunk"
)

var (
	queuedStates = []string{"pending", "testing", "tests_passed", "pending_failure"}
	outStates    = []string{"failed", "cancelled"}
	ourCancels   = []string{"user_requested", "pr_pushed_to", "pr_closed"}
)

type TrunkRead struct {
	Runner     shell.Runner
	KeyCommand string
	URL        string
	HTTP       *http.Client
	Repo       string
	Landing    string
}

type Submitted struct {
	ID       string `json:"id"`
	State    string `json:"state"`
	Head     string `json:"prSha"`
	Timeline []struct {
		Failure *struct {
			Reason string `json:"reason"`
		} `json:"failure"`
		Cancellation *struct {
			Reason string `json:"reason"`
		} `json:"cancellation"`
	} `json:"timeline"`
}

func (t TrunkRead) Read(ctx context.Context, number int) (Submitted, error) {
	key, err := t.Runner.Run(ctx, shell.Command{Argv: []string{"sh", "-c", t.KeyCommand}, Timeout: time.Minute})
	if err != nil {
		return Submitted{}, err
	}
	owner, name, _ := strings.Cut(t.Repo, "/")
	body, err := json.Marshal(map[string]any{
		"repo":            map[string]string{"host": "github.com", "owner": owner, "name": name},
		"targetBranch":    t.Landing,
		"pr":              map[string]int{"number": number},
		"includeTimeline": true,
	})
	if err != nil {
		return Submitted{}, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, t.URL, bytes.NewReader(body))
	if err != nil {
		return Submitted{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-api-token", strings.TrimSpace(key.Stdout))
	resp, err := t.HTTP.Do(req)
	if err != nil {
		return Submitted{}, err
	}
	defer func() { _ = resp.Body.Close() }()
	switch resp.StatusCode {
	case http.StatusOK:
	case http.StatusNotFound:
		return Submitted{}, nil
	default:
		return Submitted{}, fmt.Errorf("trunk read of #%d: %s", number, resp.Status)
	}
	var read Submitted
	return read, json.NewDecoder(resp.Body).Decode(&read)
}

func (s Submitted) outcome() (string, machine.Eviction) {
	switch {
	case slices.Contains(queuedStates, s.State):
		return QueueIn, ""
	case !slices.Contains(outStates, s.State):
		return "", ""
	}
	for i := len(s.Timeline) - 1; i >= 0; i-- {
		failure, cancellation := s.Timeline[i].Failure, s.Timeline[i].Cancellation
		switch {
		case failure != nil && strings.Contains(failure.Reason, "merge_conflict"):
			return QueueOut, machine.EvictionConflict
		case failure != nil && failure.Reason == "test_run_timeout":
			return QueueOut, machine.EvictionFlaky
		case failure != nil:
			return QueueOut, machine.EvictionOwnRed
		case cancellation != nil && slices.Contains(ourCancels, cancellation.Reason):
			return QueueOut, machine.EvictionOurs
		case cancellation != nil:
			return QueueOut, machine.EvictionFlaky
		}
	}
	return QueueOut, machine.EvictionFlaky
}
