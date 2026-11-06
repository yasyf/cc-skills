package grader

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	JobFile     = "job.json"
	PostsFile   = "posts.jsonl"
	ResultFile  = "result.json"
	AnswersFile = "answers.json"
	LandedFile  = "landed.json"
	CardsFile   = "cards.md"
	bodyFile    = "record.md"
	rowsFile    = "rows.md"
	readTimeout = 120 * time.Second
	planTimeout = 300 * time.Second
	gitTimeout  = 900 * time.Second
	cciText     = 400
	reasonWords = 12
	baseSubject = 500
	reasonless  = "no reasoning in `swept`, which gives one sentence per group of related hunks saying what it changes and why it holds or fails"
	nothing     = "(none)"
	gitlinkMode = "160000"
	absentIndex = "GIT_INDEX_FILE=/nonexistent/lr-no-index"
)

var (
	retryBackoff    = []time.Duration{2 * time.Second, 5 * time.Second, 10 * time.Second}
	rateLimitFloors = []time.Duration{5 * time.Second, 10 * time.Second, 20 * time.Second, 40 * time.Second, 60 * time.Second, 60 * time.Second}
	blobFetch       = []string{"-c", "fetch.negotiationAlgorithm=noop", "fetch", "-q", "--no-tags", "--no-write-fetch-head", "--recurse-submodules=no", "--filter=blob:none", "--stdin", "origin"}

	zeroRef     = regexp.MustCompile(`bad object (?:0{40}|worktrees/\S+/HEAD)`)
	looseRef    = regexp.MustCompile(`^(?:[0-9a-f]{40}|ref: \S+)\s*$`)
	fullSHA     = regexp.MustCompile(`^[0-9a-f]{40}$`)
	rangePair   = regexp.MustCompile(`(?m)^\s*(?:\d+|-+):\s+([0-9a-f]+|-+)\s+([=!<>])\s+(?:\d+|-+):\s+([0-9a-f]+|-+)[ \t]*(.*)$`)
	diffFile    = regexp.MustCompile(`^diff --git a/\S+ b/(\S+)$`)
	hunkHeader  = regexp.MustCompile(`^@@ -\S+ \+(\d+)(?:,(\d+))? @@`)
	cite        = regexp.MustCompile(`([\w./@+-]+):(\d+)(?:-(\d+))?`)
	bodyComment = regexp.MustCompile(`(?im)^(?:#{1,6}\s*|\*\*)?(?:Rulings|Regression tests|Caller trace|Body addendum)\b`)
	rowKey      = regexp.MustCompile(`(?m)^### (\S+) \|`)
	rowHeading  = regexp.MustCompile(`(?m)^##+ `)
	rowNamed    = regexp.MustCompile(`\brow (\S+) missing:`)
	replyFile   = regexp.MustCompile(`(?m)^REPLY_FILE: (\S+)$`)
	rateLimit   = regexp.MustCompile(`Rate limit reached .*?Please try again in ((?:\d+(?:\.\d+)?(?:ms|s|m|h))+)\b`)
)

type Deps struct {
	Runner shell.Runner
	Post   func(context.Context, client.Draft) (client.Record, error)
	Sleep  func(time.Duration)
	Now    func() time.Time
	Log    *slog.Logger
	Bin    string
	Config string
}

type Job struct {
	Drive    string           `json:"drive"`
	Repo     string           `json:"repo"`
	PR       string           `json:"pr"`
	Head     string           `json:"head"`
	Lane     string           `json:"lane"`
	To       []string         `json:"to"`
	Checkout string           `json:"checkout"`
	Config   string           `json:"config"`
	Model    string           `json:"model"`
	Fast     bool             `json:"fast"`
	Settled  map[string]int64 `json:"settled"`
	Sprite   string           `json:"sprite"`
	Picks    []store.Rule     `json:"picks"`
	BugItems
	Unread []string `json:"unread,omitempty"`
}

type BugItems struct {
	Items        []Record `json:"items"`
	UnknownItems []string `json:"unknown_items"`
}

func (j *Job) pull() string {
	return j.Repo + "#" + j.PR
}

func (j *Job) inputsArrived() error {
	if len(j.Unread) == 0 {
		return nil
	}
	return &JobFailed{"graded nothing, since these did not reach the pass: " + strings.Join(j.Unread, "; ")}
}

type comment struct {
	Author struct {
		Login string `json:"login"`
	} `json:"author"`
	Body string `json:"body"`
}

type pullRequest struct {
	State       string    `json:"state"`
	Title       string    `json:"title"`
	BaseRefName string    `json:"baseRefName"`
	HeadRefOid  string    `json:"headRefOid"`
	Body        string    `json:"body"`
	Comments    []comment `json:"comments"`
	Author      struct {
		Login string `json:"login"`
	} `json:"author"`
}

func (v pullRequest) authorComments() string {
	var bodies []string
	for _, c := range v.Comments {
		if c.Author.Login == v.Author.Login {
			bodies = append(bodies, strings.TrimSpace(c.Body))
		}
	}
	return joinedOrNone(bodies)
}

func (v pullRequest) rulingsComments() string {
	var bodies []string
	for _, c := range v.Comments {
		if bodyComment.MatchString(c.Body) {
			bodies = append(bodies, strings.TrimSpace(c.Body))
		}
	}
	return joinedOrNone(bodies)
}

func joinedOrNone(bodies []string) string {
	if len(bodies) == 0 {
		return nothing
	}
	return strings.Join(bodies, "\n\n---\n\n")
}

type pass struct {
	Deps
	ctx context.Context
	dir string
	job *Job
}

type future[T any] struct {
	done  chan struct{}
	value T
	err   error
}

func async[T any](work func() (T, error)) *future[T] {
	f := &future[T]{done: make(chan struct{})}
	go func() {
		defer close(f.done)
		f.value, f.err = work()
	}()
	return f
}

func (f *future[T]) result() (T, error) {
	<-f.done
	return f.value, f.err
}

func (p *pass) git(args ...string) (string, error) {
	return p.gitFed("", args...)
}

func (p *pass) gitFed(stdin string, args ...string) (string, error) {
	out, err := p.execute(shell.Command{Argv: append([]string{"git"}, args...), Stdin: stdin, Dir: p.job.Checkout, Env: []string{absentIndex}, Timeout: gitTimeout})
	return strings.TrimSpace(out), err
}

func (p *pass) retried(attempt func() (string, error), transient func(*ExitError) bool) (string, error) {
	for _, pause := range retryBackoff {
		out, err := attempt()
		if err == nil {
			return out, nil
		}
		var exit *ExitError
		if !errors.As(err, &exit) || !transient(exit) {
			return "", err
		}
		p.Sleep(pause)
	}
	return attempt()
}

func anyFailure(*ExitError) bool {
	return true
}

func (p *pass) rateLimited(attempt func(retry int) (string, error)) (string, error) {
	for retry := 0; ; retry++ {
		out, err := attempt(retry)
		if err == nil {
			return out, nil
		}
		var exit *ExitError
		if !errors.As(err, &exit) {
			return "", err
		}
		limit := rateLimit.FindStringSubmatch(exit.output())
		if limit == nil {
			return "", err
		}
		told, err := time.ParseDuration(limit[1])
		if err != nil {
			return "", err
		}
		p.Sleep(max(told, rateLimitFloors[min(retry, len(rateLimitFloors)-1)]))
	}
}

func (p *pass) viewPR() (pullRequest, error) {
	argv := []string{"ccx", "vcs", "gh", "--max-age", "0s", "--", "pr", "view", p.job.PR, "--repo", p.job.Repo, "--json", "state,title,baseRefName,headRefOid,body,comments,author"}
	out, err := p.retried(func() (string, error) { return p.run(argv, "", readTimeout) }, anyFailure)
	if err != nil {
		return pullRequest{}, err
	}
	var view pullRequest
	err = json.Unmarshal([]byte(out), &view)
	return view, err
}

func (p *pass) fetch(shas ...string) error {
	args := append([]string{"fetch", "--no-write-fetch-head", "-q", "origin"}, shas...)
	_, err := p.retried(func() (string, error) { return p.git(args...) }, p.repairedZeroRef)
	return err
}

func (p *pass) repairedZeroRef(exit *ExitError) bool {
	if !zeroRef.MatchString(exit.Stderr) {
		return false
	}
	if err := p.dropBrokenRefs(); err != nil {
		p.Log.Warn("dropping the loose refs that hold no ref failed", "checkout", p.job.Checkout, "error", err)
	}
	return true
}

func (p *pass) dropBrokenRefs() error {
	common, err := p.git("rev-parse", "--path-format=absolute", "--git-common-dir")
	if err != nil {
		return err
	}
	refs, err := os.OpenRoot(filepath.Join(common, "refs"))
	if err != nil {
		return err
	}
	defer refs.Close()
	return fs.WalkDir(refs.FS(), ".", func(path string, entry fs.DirEntry, err error) error {
		if err != nil || entry.IsDir() || strings.HasSuffix(path, ".lock") {
			return err
		}
		content, err := refs.ReadFile(path)
		if errors.Is(err, fs.ErrNotExist) {
			return nil
		}
		if err != nil || looseRef.Match(content) {
			return err
		}
		p.Log.Warn("dropping a loose ref that holds no ref", "ref", "refs/"+path)
		return refs.Remove(path)
	})
}

func (p *pass) tips(branch string) (string, string, error) {
	out, err := p.git("ls-remote", "origin", "HEAD", "refs/heads/"+branch)
	if err != nil {
		return "", "", err
	}
	refs := map[string]string{}
	for line := range strings.SplitSeq(out, "\n") {
		if sha, ref, found := strings.Cut(line, "\t"); found {
			refs[ref] = sha
		}
	}
	trunk, known := refs["HEAD"]
	if !known {
		return "", "", fmt.Errorf("ls-remote named no HEAD: %q", out)
	}
	if base, known := refs["refs/heads/"+branch]; known {
		return base, trunk, nil
	}
	return trunk, trunk, nil
}

func (p *pass) checkedOut(view pullRequest) (string, string, error) {
	base, trunk, err := p.tips(view.BaseRefName)
	if err != nil {
		return "", "", err
	}
	if err := p.fetch(p.job.Head, base, trunk); err != nil {
		return "", "", err
	}
	parent, err := p.parent(base, trunk)
	return parent, trunk, err
}

func (p *pass) parent(base, trunk string) (string, error) {
	head := p.job.Head
	_, err := p.git("merge-base", "--is-ancestor", base, head)
	if err == nil {
		return base, nil
	}
	if !exited(err) {
		return "", err
	}
	listed, err := p.git("ls-remote", "origin", "refs/pull/*/head")
	if err != nil {
		return "", err
	}
	pulls := map[string]bool{}
	for line := range strings.SplitSeq(listed, "\n") {
		sha, _, _ := strings.Cut(line, "\t")
		pulls[sha] = true
	}
	history, err := p.git("rev-list", "--topo-order", head, "^"+trunk)
	if err != nil {
		return "", err
	}
	for _, sha := range strings.Fields(history) {
		if sha != head && pulls[sha] {
			return sha, nil
		}
	}
	return base, nil
}

func (p *pass) beyond(others []string, head string) ([]string, error) {
	readings := make([]*future[string], 0, len(others))
	seen := map[string]bool{}
	for _, other := range others {
		if seen[other] {
			continue
		}
		seen[other] = true
		readings = append(readings, async(func() (string, error) { return p.ownSince(other, head) }))
	}
	var first []string
	var rest [][]string
	for index, reading := range readings {
		commits, err := reading.result()
		if err != nil {
			return nil, err
		}
		if index == 0 {
			first = strings.Fields(commits)
			continue
		}
		rest = append(rest, strings.Fields(commits))
	}
	own := make([]string, 0, len(first))
	for _, sha := range first {
		if !slices.ContainsFunc(rest, func(commits []string) bool { return !slices.Contains(commits, sha) }) {
			own = append(own, sha)
		}
	}
	return own, nil
}

func (p *pass) earlierOwn(base, trunk, last string) ([]string, error) {
	earlier, err := p.beyond([]string{base, trunk}, last)
	if err != nil || len(earlier) == 0 {
		return earlier, err
	}
	carried, err := p.git("log", "--format=%s", "-n", strconv.Itoa(baseSubject), base)
	if err != nil {
		return nil, err
	}
	listed, err := p.gitFed(lines(earlier), "log", "--no-walk=unsorted", "--format=%s", "--stdin")
	if err != nil {
		return nil, err
	}
	moved := strings.Split(carried, "\n")
	own := make([]string, 0, len(earlier))
	for index, subject := range strings.Split(listed, "\n") {
		if !slices.Contains(moved, subject) {
			own = append(own, earlier[index])
		}
	}
	return own, nil
}

func (p *pass) ownSince(other, head string) (string, error) {
	listed, err := p.git("log", "--no-merges", "--no-renames", "--format=", "--name-only", other+".."+head)
	if err != nil {
		return "", err
	}
	span := []string{other + "..." + head, "--"}
	seen := map[string]bool{}
	for path := range strings.SplitSeq(listed, "\n") {
		if path != "" && !seen[path] {
			seen[path] = true
			span = append(span, path)
		}
	}
	if err := p.prefetch(span); err != nil {
		return "", err
	}
	return p.gitFed(lines(span), "--no-lazy-fetch", "rev-list", "--cherry-pick", "--right-only", "--no-merges", "--reverse", "--stdin")
}

func lines(words []string) string {
	return strings.Join(words, "\n") + "\n"
}

func (p *pass) prefetch(walks ...[]string) error {
	seen := map[string]bool{}
	var blobs strings.Builder
	for _, walk := range walks {
		raw, err := p.gitFed(lines(walk), "log", "--raw", "--no-abbrev", "--no-merges", "--no-renames", "--format=", "--stdin")
		if err != nil {
			return err
		}
		for line := range strings.SplitSeq(raw, "\n") {
			fields := strings.Fields(line)
			if len(fields) < 4 || !strings.HasPrefix(fields[0], ":") {
				continue
			}
			for _, side := range [][2]string{{fields[0][1:], fields[2]}, {fields[1], fields[3]}} {
				if side[0] != gitlinkMode && strings.Trim(side[1], "0") != "" && !seen[side[1]] {
					seen[side[1]] = true
					blobs.WriteString(side[1] + "\n")
				}
			}
		}
	}
	if blobs.Len() == 0 {
		return nil
	}
	checked, err := p.gitFed(blobs.String(), "--no-lazy-fetch", "cat-file", "--batch-check")
	if err != nil {
		return err
	}
	var missing strings.Builder
	for line := range strings.SplitSeq(checked, "\n") {
		if oid, absent := strings.CutSuffix(line, " missing"); absent {
			missing.WriteString(oid + "\n")
		}
	}
	if missing.Len() == 0 {
		return nil
	}
	_, err = p.gitFed(missing.String(), blobFetch...)
	return err
}

func (p *pass) resolve(name string) (string, error) {
	out, err := p.git("--no-lazy-fetch", "rev-parse", "--verify", "--quiet", name+"^{commit}")
	if exited(err) {
		return "", nil
	}
	return out, err
}

func (p *pass) fetched(name string) (string, error) {
	full, err := p.resolve(name)
	if err != nil || full != "" || !fullSHA.MatchString(name) {
		return full, err
	}
	if err := p.fetch(name); err != nil {
		if exited(err) {
			return "", nil
		}
		return "", err
	}
	return p.resolve(name)
}

type pair struct {
	mark string
	sha  string
}

func (p *pass) rangePairs(base, last string, earlier, own []string) (string, []pair, error) {
	ranges := []string{earlier[0] + "^.." + last, own[0] + "^.." + p.job.Head}
	if err := p.prefetch(ranges[:1], ranges[1:]); err != nil {
		return "", nil, err
	}
	pairs := strings.Join(ranges, " ")
	out, err := p.git(append([]string{"--no-lazy-fetch", "range-diff", "--no-patch", "--no-color"}, ranges...)...)
	if err != nil {
		return "", nil, err
	}
	marks := rangePair.FindAllStringSubmatch(out, -1)
	moved := map[string]bool{}
	if slices.ContainsFunc(marks, func(found []string) bool { return found[2] == "<" }) {
		subjects, err := p.git("log", "--format=%s", "-n", strconv.Itoa(baseSubject), base)
		if err != nil {
			return "", nil, err
		}
		for subject := range strings.SplitSeq(subjects, "\n") {
			moved[subject] = true
		}
	}
	var changed []pair
	for _, found := range marks {
		left, mark, sha, subject := found[1], found[2], found[3], found[4]
		if mark == ">" {
			changed = append(changed, pair{mark, sha})
			continue
		}
		if mark == "=" || (mark == "<" && moved[strings.TrimSpace(subject)]) {
			continue
		}
		carried, err := p.dropsCarried(base, left, sha)
		if err != nil {
			return "", nil, err
		}
		if carried {
			continue
		}
		if mark == "<" {
			changed = append(changed, pair{mark, left})
			continue
		}
		changed = append(changed, pair{mark, sha})
	}
	return pairs, changed, nil
}

type patchLine struct {
	path string
	sign byte
	text string
}

func (p *pass) patchLines(sha string) (map[patchLine]bool, error) {
	out, err := p.git("show", "--format=", "--no-color", "--no-ext-diff", sha)
	if err != nil {
		return nil, err
	}
	lines := map[patchLine]bool{}
	path := ""
	for line := range strings.SplitSeq(out, "\n") {
		if found := diffFile.FindStringSubmatch(line); found != nil {
			path = found[1]
			continue
		}
		if line == "" || (line[0] != '+' && line[0] != '-') || strings.HasPrefix(line, "+++") || strings.HasPrefix(line, "---") {
			continue
		}
		if text := squeezed(line[1:]); text != "" {
			lines[patchLine{path, line[0], text}] = true
		}
	}
	return lines, nil
}

func rowKeys(rows string) []string {
	matches := rowKey.FindAllStringSubmatch(rows, -1)
	keys := make([]string, 0, len(matches))
	for _, found := range matches {
		keys = append(keys, found[1])
	}
	return keys
}

func citedRows(rows string, keys []string) string {
	var cited strings.Builder
	var seen []string
	for _, at := range rowKey.FindAllStringSubmatchIndex(rows, -1) {
		key := rows[at[2]:at[3]]
		if !slices.Contains(keys, key) || slices.Contains(seen, key) {
			continue
		}
		seen = append(seen, key)
		block := rows[at[0]:]
		if first := strings.IndexByte(block, '\n'); first >= 0 {
			if next := rowHeading.FindStringIndex(block[first+1:]); next != nil {
				block = block[:first+1+next[0]]
			}
		}
		cited.WriteString("\n\n" + strings.TrimSpace(block))
	}
	if cited.Len() == 0 {
		return ""
	}
	return "\n\nThe rows this grade cites, whole, as its rows file gave them:" + cited.String() + "\n"
}

func squeezed(text string) string {
	return strings.Join(strings.Fields(text), " ")
}

func (p *pass) dropsCarried(base, left, right string) (bool, error) {
	earlier, err := p.patchLines(left)
	if err != nil {
		return false, err
	}
	later := map[patchLine]bool{}
	if strings.Trim(right, "-") != "" {
		if later, err = p.patchLines(right); err != nil {
			return false, err
		}
	}
	for line := range later {
		if !earlier[line] {
			return false, nil
		}
	}
	texts := map[string]map[string]bool{}
	for line := range earlier {
		if later[line] {
			continue
		}
		if _, read := texts[line.path]; !read {
			if texts[line.path], err = p.baseLines(base, line.path); err != nil {
				return false, err
			}
		}
		if texts[line.path][line.text] != (line.sign == '+') {
			return false, nil
		}
	}
	return true, nil
}

func (p *pass) baseLines(base, path string) (map[string]bool, error) {
	out, err := p.git("show", base+":"+path)
	lines := map[string]bool{}
	if exited(err) {
		return lines, nil
	}
	if err != nil {
		return nil, err
	}
	for line := range strings.SplitSeq(out, "\n") {
		lines[squeezed(line)] = true
	}
	return lines, nil
}

func (p *pass) rulings(verb string, timeout time.Duration, args ...string) *future[string] {
	argv := append([]string{filepath.Join(p.Bin, "rulings.py"), verb, "--repo", p.job.Checkout, "--drive", p.job.Drive}, args...)
	return async(func() (string, error) {
		return p.execute(shell.Command{Argv: argv, Env: []string{"CLAUDE_CONFIG_DIR=" + p.job.Config}, Timeout: timeout})
	})
}

func (p *pass) planRead() *future[string] {
	return p.rulings("plan", planTimeout)
}

func planOnThisBox(reading *future[string]) (string, error) {
	planned, err := reading.result()
	if err != nil {
		return "", err
	}
	plan := strings.TrimSpace(planned)
	if plan == "" {
		return "", nil
	}
	if _, err := os.Stat(plan); err != nil {
		return "", &JobFailed{fmt.Sprintf("graded nothing, since the plan the register names is not on this box: %v", err)}
	}
	return plan, nil
}

func (p *pass) sliceRows(head, branch string, flags ...string) *future[string] {
	return p.rulings("slice", gitTimeout, append([]string{"--required", "--pr", p.job.pull(), "--head", head, "--base", branch, "--answers", filepath.Join(p.dir, AnswersFile)}, flags...)...)
}

func (p *pass) touched(start, head string) (touched, error) {
	out, err := p.git("diff", "--unified=0", "--no-color", "--no-ext-diff", start, head)
	if err != nil {
		return nil, err
	}
	return hunks(out), nil
}

func (p *pass) audience(also ...string) string {
	var names []string
	for _, name := range append(append([]string{p.job.Lane}, p.job.To...), also...) {
		if name != "" && !slices.Contains(names, name) {
			names = append(names, name)
		}
	}
	return strings.Join(names, ",")
}

func (p *pass) posted(argv []string) (string, error) {
	draft, err := draftOf(argv)
	if err != nil {
		return "", err
	}
	line, err := json.Marshal(argv)
	if err != nil {
		return "", err
	}
	posts, err := os.OpenFile(filepath.Join(p.dir, PostsFile), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return "", err
	}
	defer posts.Close()
	if _, err := posts.Write(append(line, '\n')); err != nil {
		return "", err
	}
	if p.job.Sprite != "" {
		body, err := os.Open(draft.Path)
		if err != nil {
			return "", err
		}
		defer body.Close()
		return "", errors.Join(body.Sync(), posts.Sync())
	}
	stored, err := p.Post(p.ctx, draft)
	var down *client.BusDown
	if errors.As(err, &down) {
		p.Log.Warn("the store did not take a post; the runner reposts the pass's logged records when it settles", "id", draft.ID, "error", err)
		return "", nil
	}
	if err != nil {
		return "", err
	}
	return "#" + strconv.FormatInt(stored.Seq, 10), nil
}

func draftOf(argv []string) (client.Draft, error) {
	var draft client.Draft
	for index := 2; index < len(argv); index++ {
		flag := argv[index]
		if flag == "--json" {
			continue
		}
		index++
		value := argv[index]
		switch flag {
		case "--drive":
			draft.Drive = value
		case "--lane":
			draft.Lane = value
		case "--kind":
			draft.Kind = value
		case "--pr":
			draft.PRs = []string{value}
		case "--id":
			draft.ID = value
		case "--path":
			draft.Path = value
		case "--to":
			draft.To = strings.Split(value, ",")
		case "--text":
			draft.Text = value
		case "--attr":
			name, said, _ := strings.Cut(value, "=")
			if draft.Attrs == nil {
				draft.Attrs = map[string]string{}
			}
			draft.Attrs[name] = said
		default:
			return client.Draft{}, fmt.Errorf("a desk post names %s, which no grader writes", flag)
		}
	}
	return draft, nil
}

func Repost(ctx context.Context, dir string, post func(context.Context, client.Draft) (client.Record, error)) error {
	data, err := os.ReadFile(filepath.Join(dir, PostsFile))
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	var failures []error
	for line := range strings.Lines(string(data)) {
		var argv []string
		if err := json.Unmarshal([]byte(line), &argv); err != nil {
			failures = append(failures, err)
			continue
		}
		draft, err := draftOf(argv)
		if err != nil {
			failures = append(failures, err)
			continue
		}
		if _, err := post(ctx, draft); err != nil {
			failures = append(failures, err)
		}
	}
	return errors.Join(failures...)
}

func (p *pass) ask(model, schema, session, question string, timeout time.Duration) (string, error) {
	argv := []string{"codex-ask", "-m", model}
	if model == solModel {
		argv = append(argv, "--effort", solEffort)
	}
	if p.job.Fast {
		argv = append(argv, "--incident")
	}
	argv = append(argv, "--schema", schema, "-s", session, question)
	return p.run(argv, p.job.Checkout, timeout)
}

func replyText(out string) (string, error) {
	found := replyFile.FindStringSubmatch(out)
	if found == nil {
		return "", fmt.Errorf("codex-ask named no reply file: %q", out)
	}
	data, err := os.ReadFile(found[1])
	return string(data), err
}

func clipped(text string) string {
	runes := []rune(text)
	if len(runes) <= cciText {
		return text
	}
	return string(runes[:cciText-1]) + "…"
}

func writeResult(dir string, outcome any) error {
	data, err := json.Marshal(outcome)
	if err != nil {
		return err
	}
	staged := filepath.Join(dir, fmt.Sprintf(".%s.%d", ResultFile, os.Getpid()))
	file, err := os.Create(staged)
	if err != nil {
		return err
	}
	defer file.Close()
	if _, err := file.Write(append(data, '\n')); err != nil {
		return err
	}
	if err := file.Sync(); err != nil {
		return err
	}
	return os.Rename(staged, filepath.Join(dir, ResultFile))
}

type failedResult struct {
	Error string `json:"error"`
	Lost  bool   `json:"no_result,omitempty"`
}

const codexWorkerLost = 75

func settle[T any](dir string, outcome T, err error) error {
	if err == nil {
		return writeResult(dir, outcome)
	}
	var exit *ExitError
	lost := errors.As(err, &exit) && exit.Argv[0] == "codex-ask" && exit.Code == codexWorkerLost
	if written := writeResult(dir, failedResult{err.Error(), lost}); written != nil {
		return errors.Join(err, written)
	}
	return err
}

type JobFailed struct {
	Why string
}

func (e *JobFailed) Error() string {
	return "JobFailed: " + e.Why
}

func short(sha string, width int) string {
	return sha[:min(len(sha), width)]
}

func dated(at time.Time) string {
	return at.Format("2006-01-02")
}

func NamesHead(named, sha string) bool {
	return sha != "" && (strings.HasPrefix(sha, named) || strings.HasPrefix(named, sha))
}

func wordsBefore(text string, stop *regexp.Regexp) int {
	if at := stop.FindStringIndex(text); at != nil {
		text = text[:at[0]]
	}
	return len(strings.Fields(text))
}
