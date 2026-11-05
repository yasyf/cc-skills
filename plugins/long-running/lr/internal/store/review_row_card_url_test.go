package store

import (
	"encoding/json"
	"testing"
)

func TestAMonorepoRowNamesTheCardHostSlackKeepsAndOtherReposDoNot(t *testing.T) {
	for _, tc := range []struct {
		name string
		row  ReviewRow
		want string
	}{
		{"monorepo", ReviewRow{Repo: "Forge-AI/monorepo", Number: 42}, "https://oncall.poetic.com/pr/42"},
		{"other repo", ReviewRow{Repo: "yasyf/cc-slack", Number: 42}, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			raw, err := json.Marshal(tc.row)
			if err != nil {
				t.Fatal(err)
			}
			var got struct {
				Repo    string `json:"repo"`
				CardURL string `json:"card_url"`
			}
			if err := json.Unmarshal(raw, &got); err != nil {
				t.Fatal(err)
			}
			if got.Repo != tc.row.Repo || got.CardURL != tc.want {
				t.Fatalf("row = %s, want repo %q and card_url %q", raw, tc.row.Repo, tc.want)
			}
		})
	}
}
