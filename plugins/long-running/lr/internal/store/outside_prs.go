package store

import "database/sql"

const (
	OutsideSearchCursor = "outside-search"
	outsidePRColumns    = "repo, number, title, head, state, old_tree, no_port, port"
)

type OutsidePR struct {
	Repo    string `json:"repo"`
	Number  int    `json:"number"`
	Title   string `json:"title"`
	Head    string `json:"head"`
	State   string `json:"state"`
	OldTree string `json:"old_tree"`
	NoPort  string `json:"no_port"`
	Port    string `json:"port"`
}

func (t *Tx) OutsidePRs(repo string) (map[int]OutsidePR, error) {
	rows, err := t.tx.Query("SELECT "+outsidePRColumns+" FROM outside_prs WHERE repo = ?", repo)
	prs, err := collect(rows, err, func(rows *sql.Rows) (OutsidePR, error) {
		var pr OutsidePR
		err := rows.Scan(&pr.Repo, &pr.Number, &pr.Title, &pr.Head, &pr.State, &pr.OldTree, &pr.NoPort, &pr.Port)
		return pr, err
	})
	if err != nil {
		return nil, err
	}
	byNumber := make(map[int]OutsidePR, len(prs))
	for _, pr := range prs {
		byNumber[pr.Number] = pr
	}
	return byNumber, nil
}

func (t *Tx) PutOutsidePRs(prs map[int]OutsidePR) error {
	for _, pr := range prs {
		if _, err := t.tx.Exec("INSERT OR REPLACE INTO outside_prs ("+outsidePRColumns+") VALUES ("+placeholders(8)+")", pr.Repo, pr.Number, pr.Title, pr.Head, pr.State, pr.OldTree, pr.NoPort, pr.Port); err != nil {
			return err
		}
	}
	return nil
}

func (t *Tx) DeleteOutsidePR(repo string, number int) error {
	_, err := t.tx.Exec("DELETE FROM outside_prs WHERE repo = ? AND number = ?", repo, number)
	return err
}

func (s *Store) OutsidePRs(repo string) (prs map[int]OutsidePR, err error) {
	err = s.Update(func(t *Tx) error {
		prs, err = t.OutsidePRs(repo)
		return err
	})
	return prs, err
}
