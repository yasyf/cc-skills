package facts

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"strconv"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const authorNameFact = "author-name"

type Whose struct {
	Number  int    `json:"number"`
	Author  string `json:"author"`
	Name    string `json:"name,omitempty"`
	Lane    string `json:"lane,omitempty"`
	Ours    bool   `json:"ours"`
	Foreign bool   `json:"foreign"`
}

func (s Source) Whose(ctx context.Context, numbers []int) ([]Whose, error) {
	owners, err := s.Store.OwnerOf(numbers)
	if err != nil {
		return nil, err
	}
	out := make([]Whose, 0, len(numbers))
	for _, number := range numbers {
		known, err := s.Store.PRFacts(s.Repo, number)
		if err != nil {
			return nil, err
		}
		if known[store.AuthorFact] == "" {
			if known[store.AuthorFact], known[authorNameFact], err = s.author(ctx, number); err != nil {
				return nil, fmt.Errorf("#%d author: %w", number, err)
			}
		}
		f := machine.Facts{Lane: cmp.Or(owners[number], known["lane"]), Tap: machine.Tap(known["tap"])}
		f.Exempt = gates.TaplessClass(known["exempt"])
		if known[store.AuthorFact] != s.Author {
			f.ForeignAuthor = known[store.AuthorFact]
		}
		out = append(out, Whose{number, known[store.AuthorFact], known[authorNameFact], f.Lane, f.Ours(), f.Foreign()})
	}
	return out, nil
}

func (s Source) author(ctx context.Context, number int) (string, string, error) {
	out, err := s.CCX.GH(ctx, ccx.MaxAgeCached, "pr", "view", strconv.Itoa(number), "--repo", s.Repo, "--json", "author")
	if err != nil {
		return "", "", err
	}
	var pr struct {
		Author struct {
			Login string `json:"login"`
			Name  string `json:"name"`
		} `json:"author"`
	}
	if err := json.Unmarshal([]byte(out), &pr); err != nil {
		return "", "", err
	}
	for kind, value := range map[string]string{store.AuthorFact: pr.Author.Login, authorNameFact: pr.Author.Name} {
		if _, err := s.Store.PutFact(s.Repo, number, "", kind, value, s.Now()); err != nil {
			return "", "", err
		}
	}
	return pr.Author.Login, pr.Author.Name, nil
}
