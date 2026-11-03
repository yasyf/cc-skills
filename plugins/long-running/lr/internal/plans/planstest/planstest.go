package planstest

import (
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"slices"
	"strings"
	"sync"
)

const (
	Program     = "brook"
	PlanID      = "fc8714e000000000000000000000000000000000"
	OtherPlanID = "01a07ac000000000000000000000000000000000"
	PlanBody    = "# Migration\n\n## Approach\n### 1. Readers first\nbody\n### 2. Delete the old store\nbody\n#### 3. Not a step\n## Appendix\n### 1. Readers first, again\n"
)

type Fields map[string]any

func Task(ident, title string, fields Fields) Fields {
	task := Fields{
		"id":         strings.Repeat(ident, 40),
		"title":      title,
		"type":       "task",
		"status":     "open",
		"priority":   2,
		"labels":     []string{"program:" + Program},
		"created_at": "2026-10-01T00:00:00Z",
		"updated_at": "2026-10-01T00:00:00Z",
	}
	maps.Copy(task, fields)
	return task
}

func Plan(ident, title string) Fields {
	return Fields{"id": ident, "title": title, "body": PlanBody, "status": "draft", "labels": []string{"program:" + Program}, "updated_at": "2026-10-01T00:00:00Z"}
}

type Fake struct {
	Tasks []Fields
	Plans []Fields

	mu    sync.Mutex
	calls [][]string
}

func (f *Fake) Calls() [][]string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return slices.Clone(f.calls)
}

func (f *Fake) Ccn(_ context.Context, args ...string) (string, error) {
	f.mu.Lock()
	f.calls = append(f.calls, args)
	f.mu.Unlock()
	switch {
	case len(args) >= 2 && args[0] == "task" && args[1] == "list":
		keys := []string{"id", "title", "type", "status", "priority", "branch", "assignee", "plan", "blocked_by", "updated_at"}
		return encoded(selected(f.Tasks, labels(args), keys))
	case len(args) >= 3 && args[0] == "task" && args[1] == "show":
		for _, task := range f.Tasks {
			if task["id"] == args[2] {
				return encoded(task)
			}
		}
	case len(args) >= 2 && args[0] == "plan" && args[1] == "list":
		live := []Fields{}
		for _, plan := range f.Plans {
			if slices.Contains([]any{"draft", "approved", "executing"}, plan["status"]) {
				live = append(live, plan)
			}
		}
		return encoded(selected(live, labels(args), []string{"id", "title", "status", "updated_at"}))
	case len(args) >= 3 && args[0] == "plan" && args[1] == "show":
		for _, plan := range f.Plans {
			if id, _ := plan["id"].(string); strings.HasPrefix(id, args[2]) {
				return encoded(plan)
			}
		}
	}
	return "", fmt.Errorf("unexpected ccn call: %v", args)
}

func labels(args []string) []string {
	found := []string{}
	for at, arg := range args {
		if arg == "--label" {
			found = append(found, args[at+1])
		}
	}
	return found
}

func selected(records []Fields, wanted []string, keys []string) []Fields {
	out := []Fields{}
	for _, record := range records {
		held, _ := record["labels"].([]string)
		if !containsAll(held, wanted) {
			continue
		}
		row := Fields{}
		for _, key := range keys {
			if value, ok := record[key]; ok {
				row[key] = value
			}
		}
		out = append(out, row)
	}
	return out
}

func containsAll(held, wanted []string) bool {
	for _, label := range wanted {
		if !slices.Contains(held, label) {
			return false
		}
	}
	return true
}

func encoded(value any) (string, error) {
	data, err := json.Marshal(value)
	return string(data), err
}
