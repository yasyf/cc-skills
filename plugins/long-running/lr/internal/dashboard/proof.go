package dashboard

import (
	"bytes"
	"crypto/sha1" //nolint:gosec // G505: a git object id is SHA-1 by git's format, not a security hash.
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
)

var (
	commitID  = regexp.MustCompile(`^[0-9a-f]{40}$`)
	unserved  = []string{".git", "__pycache__", ".DS_Store"}
	treeOrder = func(a, b fs.DirEntry) int { return strings.Compare(treeName(a), treeName(b)) }
)

type Proof struct {
	Commit   string `json:"commit"`
	Tree     string `json:"tree"`
	Served   string `json:"served"`
	TextOnly bool   `json:"text_only"`
	Pages    []Page `json:"pages"`
}

type Page struct {
	Page    string   `json:"page"`
	Desktop string   `json:"desktop"`
	Phone   string   `json:"phone"`
	Answers []Answer `json:"answers"`
}

type Answer struct {
	ID    string `json:"id"`
	Shows string `json:"shows"`
}

func (s Service) served() string { return filepath.Join(s.State, "dashboard.served") }

func (s Service) live() string {
	return filepath.Join(s.Claude, "long-running", "current", "dashboard")
}

func (s Service) Clear(tree, liveURL string, up bool, proof, order string) error {
	last, err := os.ReadFile(s.served())
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	if !up || string(last) == tree {
		return nil
	}
	if order != "" {
		return ordered(owner.Ledger(s.State), order)
	}
	if proof == "" {
		return fmt.Errorf("the dashboard going live is tree %s, which %s does not record as served; restart with --proof <file> naming the commit whose `git rev-parse <commit>:plugins/long-running/dashboard` is that tree, its non-live serve, a desktop and phone screenshot of each page the diff touches and the owner answers that govern them, or --ordered <m-id> for the owner's own order", tree, s.served())
	}
	return proven(proof, tree, liveURL)
}

func ordered(ledger, row string) error {
	rows, err := owner.Rows(ledger, true)
	if err != nil {
		return err
	}
	if !slices.ContainsFunc(rows, func(found string) bool { return strings.HasPrefix(found, "| "+row+" |") }) {
		return fmt.Errorf("%s holds no owner message %s, so no order of his clears this restart", ledger, row)
	}
	return nil
}

func proven(path, tree, liveURL string) error {
	body, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var p Proof
	if err := json.Unmarshal(body, &p); err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	var faults []string
	if !commitID.MatchString(p.Commit) {
		faults = append(faults, fmt.Sprintf("commit %q is no full commit sha", p.Commit))
	}
	if p.Tree != tree {
		faults = append(faults, fmt.Sprintf("tree %q is not %s, the dashboard going live", p.Tree, tree))
	}
	faults = append(faults, offLive(p.Served, liveURL)...)
	faults = append(faults, shots(p)...)
	for _, page := range p.Pages {
		if len(page.Answers) == 0 {
			faults = append(faults, fmt.Sprintf("page %q names no owner answer or ruling governing it", page.Page))
		}
		for _, a := range page.Answers {
			if a.ID == "" || strings.TrimSpace(a.Shows) == "" {
				faults = append(faults, fmt.Sprintf("page %q answer %q does not say what the page shows for it", page.Page, a.ID))
			}
		}
	}
	if len(faults) != 0 {
		return fmt.Errorf("proof %s does not clear the restart: %s", path, strings.Join(faults, "; "))
	}
	return nil
}

func offLive(served, liveURL string) []string {
	got, err := url.Parse(served)
	if err != nil || got.Port() == "" {
		return []string{fmt.Sprintf("served %q names no port", served)}
	}
	live, err := url.Parse(liveURL)
	if err != nil {
		return []string{err.Error()}
	}
	if got.Port() == live.Port() {
		return []string{fmt.Sprintf("served %s is the live port", served)}
	}
	return nil
}

func shots(p Proof) []string {
	if len(p.Pages) == 0 {
		return []string{"it shows no page"}
	}
	var faults []string
	for _, page := range p.Pages {
		taken := slices.DeleteFunc([]string{page.Desktop, page.Phone}, func(shot string) bool { return shot == "" })
		if !p.TextOnly && len(taken) < 2 {
			faults = append(faults, fmt.Sprintf("page %q lacks a desktop or phone screenshot", page.Page))
		}
		if len(taken) == 0 {
			faults = append(faults, fmt.Sprintf("page %q has no screenshot", page.Page))
		}
		for _, shot := range taken {
			if info, err := os.Stat(shot); err != nil || info.Size() == 0 {
				faults = append(faults, fmt.Sprintf("screenshot %s is missing or empty", shot))
			}
		}
	}
	return faults
}

func Tree(dir string) (string, error) {
	id, err := tree(dir)
	if err != nil {
		return "", err
	}
	if id == nil {
		return "", fmt.Errorf("%s holds no dashboard", dir)
	}
	return hex.EncodeToString(id), nil
}

func tree(dir string) ([]byte, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	slices.SortFunc(entries, treeOrder)
	var body bytes.Buffer
	for _, entry := range entries {
		if slices.Contains(unserved, entry.Name()) || strings.HasSuffix(entry.Name(), ".pyc") {
			continue
		}
		mode, id, err := object(filepath.Join(dir, entry.Name()), entry)
		if err != nil {
			return nil, err
		}
		if id == nil {
			continue
		}
		fmt.Fprintf(&body, "%s %s\x00", mode, entry.Name())
		body.Write(id)
	}
	if body.Len() == 0 {
		return nil, nil
	}
	return hashed("tree", body.Bytes()), nil
}

func object(path string, entry fs.DirEntry) (string, []byte, error) {
	switch {
	case entry.IsDir():
		id, err := tree(path)
		return "40000", id, err
	case entry.Type()&fs.ModeSymlink != 0:
		target, err := os.Readlink(path)
		return "120000", hashed("blob", []byte(target)), err
	}
	info, err := entry.Info()
	if err != nil {
		return "", nil, err
	}
	content, err := os.ReadFile(path)
	if err != nil {
		return "", nil, err
	}
	if info.Mode()&0o111 != 0 {
		return "100755", hashed("blob", content), nil
	}
	return "100644", hashed("blob", content), nil
}

func treeName(entry fs.DirEntry) string {
	if entry.IsDir() {
		return entry.Name() + "/"
	}
	return entry.Name()
}

func hashed(kind string, body []byte) []byte {
	sum := sha1.Sum(append([]byte(fmt.Sprintf("%s %d\x00", kind, len(body))), body...)) //nolint:gosec // G401: git object ids are SHA-1.
	return sum[:]
}
