package carry

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log/slog"
	"maps"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"slices"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	maxBytes = 64 << 20
	notesDir = "ccn"
	cciDir   = "cci"
	pathStop = `\s` + "`" + `'"<>()\[\]{},;:|*?$`
	template = "<{$*"
	joined   = `\w./~-`

	DriveDir = "DRIVE_DIR"
)

var (
	recordNumber = `\d+(?:@[0-9a-f]{6,16})?`
	showToken    = regexp.MustCompile(`--drive +[\w-]+|--budget +\d+|-[\w-]+(?:=\S+)?|['"]?#?(` + recordNumber + `)['"]?`)
	reference    = regexp.MustCompile(`ccn (doc|plan|task) show ([0-9A-Za-z][\w-]*)|\$\{?([A-Z_][A-Z0-9_]*)\}?/[^` + pathStop + `]+|(?:~/|/)[^` + pathStop + `]+` +
		`|cci show(?P<shown>(?: +(?:` + showToken.String() + `))+)` +
		`|\brecords? +#?(?P<recorded>\d{4,}(?:@[0-9a-f]{6,16})?)\b` +
		`|\b(?P<tagged>\d+@[0-9a-f]{6,16})\b` +
		`|\b(?P<lane>[A-Za-z]\w*(?:-\w+)*)(?:'s)? +(?P<bare>\d{5,})\b`)
	assigned     = regexp.MustCompile(`(?m)(?:^|[\s;` + "`" + `])(?:export )?([A-Z_][A-Z0-9_]*)=["']?((?:~/|/)[^` + pathStop + `]+)`)
	joinedByte   = regexp.MustCompile(`[` + joined + `]`)
	QuotedRows   = regexp.MustCompile(`(?ms)^Register slice:.*?^slice-hash: [0-9a-f]+$`)
	sharedRoots  = []string{".local/state/lr-scratch", ".claude/scratch", ".vcache/scratch", ".claude/plans", "handoffs"}
	carriedRoots = append(slices.Clip(sharedRoots), ".claude/plugins/cache")
)

type source struct {
	kind, id, lane string
	variable       bool
}

type record struct {
	Lane string   `json:"lane"`
	To   []string `json:"to"`
}

type Carrier struct {
	Runner   shell.Runner
	Home     string
	Repo     string
	Checkout string
	Into     string
	Box      string
	Drive    string
	Vars     map[string]string
}

func DriveCheckout(driveDir string) string {
	loaded, err := config.Load(filepath.Join(driveDir, "lr.json"), io.Discard)
	if err != nil {
		return ""
	}
	return loaded.Checkout
}

func Command(a *app.App) *cobra.Command {
	c := Carrier{Runner: a.Runner, Vars: map[string]string{}}
	cmd := &cobra.Command{
		Use:   "carry BRIEF",
		Short: "Copy each file under this home, each ccn doc, plan or task and each cci record a brief names into --into, which lands at --box on a Sprite, and print the brief with every reference rewritten to its copy",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			brief, err := os.ReadFile(args[0])
			if err != nil {
				return err
			}
			if c.Home, err = os.UserHomeDir(); err != nil {
				return err
			}
			if file, err := a.ConfigFile(); err == nil {
				c.Vars[DriveDir] = filepath.Dir(file)
				c.Checkout = DriveCheckout(c.Vars[DriveDir])
			}
			rewritten, left := c.Carry(cmd.Context(), string(brief))
			for _, behind := range left {
				fmt.Fprintf(cmd.ErrOrStderr(), "brief %s names what stays behind: %s\n", args[0], behind)
			}
			_, err = fmt.Fprint(cmd.OutOrStdout(), rewritten)
			return err
		},
	}
	cmd.Flags().StringVar(&c.Into, "into", "", "the directory the copies are written to")
	cmd.Flags().StringVar(&c.Box, "box", "", "the absolute path that directory lands at on the Sprite")
	cmd.Flags().StringVar(&c.Repo, "repo", "", "the checkout ccn reads docs and tasks from")
	for _, name := range []string{"into", "box", "repo"} {
		_ = cmd.MarkFlagRequired(name)
	}
	return cmd
}

func (c Carrier) Carry(ctx context.Context, brief string) (string, []string) {
	rewritten, faults := c.rewrite(ctx, brief, map[source]string{})
	left := make([]string, 0, len(faults))
	for _, fault := range faults {
		left = append(left, fault.Error())
	}
	return rewritten, left
}

func (c Carrier) rewrite(ctx context.Context, brief string, copies map[source]string) (string, []error) {
	var rewritten strings.Builder
	var faults []error
	last := 0
	vars := c.varsOf(brief)
	quoted := QuotedRows.FindAllStringIndex(brief, -1)
	for _, match := range reference.FindAllStringSubmatchIndex(brief, -1) {
		if slices.ContainsFunc(quoted, func(rows []int) bool { return rows[0] <= match[0] && match[0] < rows[1] }) {
			continue
		}
		named, start, end, ok := c.named(brief, match, vars)
		if !ok {
			continue
		}
		copied := make([]string, 0, len(named))
		for _, one := range named {
			onBox, seen := copies[one]
			if !seen {
				var err error
				if onBox, err = c.carry(ctx, one); err != nil {
					faults = append(faults, err)
				}
				copies[one] = onBox
				if err == nil && isBrief(one.id) {
					if err := c.follow(ctx, one.id, copies); err != nil {
						faults = append(faults, err)
					}
				}
			}
			copied = append(copied, onBox)
		}
		if slices.Contains(copied, "") {
			continue
		}
		rewritten.WriteString(brief[last:start] + strings.Join(copied, " "))
		last = end
	}
	return rewritten.String() + brief[last:], faults
}

func isBrief(name string) bool {
	base := path.Base(name)
	return base == "brief.md" || strings.HasSuffix(base, ".brief.md")
}

func (c Carrier) follow(ctx context.Context, name string, copies map[source]string) error {
	if path.Base(name) == "brief.md" {
		if _, err := c.file(filepath.Dir(name)); err != nil {
			slog.Warn("a carried brief's directory stays behind", "brief", name, "err", err)
		}
	}
	info, err := os.Stat(name)
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	text, err := os.ReadFile(name)
	if err != nil {
		return err
	}
	rewritten, faults := c.rewrite(ctx, string(text), copies)
	for _, fault := range faults {
		slog.Warn("a carried brief names what stays behind", "brief", name, "fault", fault)
	}
	return write(filepath.Join(c.Into, name), []byte(rewritten), info.Mode().Perm())
}

func (c Carrier) varsOf(brief string) map[string]string {
	vars := map[string]string{"HOME": c.Home}
	maps.Copy(vars, c.Vars)
	for _, found := range assigned.FindAllStringSubmatch(brief, -1) {
		vars[found[1]] = found[2]
		if rest, ok := strings.CutPrefix(found[2], "~/"); ok {
			vars[found[1]] = c.Home + "/" + rest
		}
	}
	return vars
}

func (c Carrier) named(brief string, match []int, vars map[string]string) ([]source, int, int, bool) {
	if match[2] >= 0 {
		return []source{{kind: brief[match[2]:match[3]], id: brief[match[4]:match[5]]}}, match[0], match[1], true
	}
	if cited, start, ok := c.cited(brief, match); ok {
		return cited, start, match[1], true
	}
	start, end := match[0], match[1]
	token := brief[start:end]
	if start > 0 && joinedByte.MatchString(brief[start-1:start]) || end < len(brief) && strings.ContainsRune(template, rune(brief[end])) {
		return nil, 0, 0, false
	}
	if strings.Contains(token, "…") || strings.HasSuffix(token, "..") {
		return nil, 0, 0, false
	}
	token = strings.TrimSuffix(token, ".")
	name := token
	variable := match[6] >= 0
	if variable {
		value, known := vars[brief[match[6]:match[7]]]
		if !known {
			return nil, 0, 0, false
		}
		name = value + token[strings.Index(token, "/"):]
	} else if rest, ok := strings.CutPrefix(token, "~/"); ok {
		name = c.Home + "/" + rest
	}
	if !strings.HasPrefix(name, c.Home+"/") || name == c.Home+"/" {
		return nil, 0, 0, false
	}
	return []source{{id: name, variable: variable}}, start, start + len(token), true
}

func (c Carrier) cited(brief string, match []int) ([]source, int, bool) {
	span := func(name string) (string, int) {
		i := reference.SubexpIndex(name)
		if match[2*i] < 0 {
			return "", 0
		}
		return brief[match[2*i]:match[2*i+1]], match[2*i]
	}
	if shown, _ := span("shown"); shown != "" {
		var cited []source
		for _, token := range showToken.FindAllStringSubmatch(shown, -1) {
			if token[1] != "" {
				cited = append(cited, source{kind: cciDir, id: token[1]})
			}
		}
		return cited, match[0], len(cited) > 0
	}
	if id, start := span("recorded"); id != "" {
		return []source{{kind: cciDir, id: id}}, start, true
	}
	if id, start := span("tagged"); id != "" {
		if start > 0 && brief[start-1] == '#' {
			start--
		}
		return []source{{kind: cciDir, id: id}}, start, true
	}
	if id, start := span("bare"); id != "" {
		lane, _ := span("lane")
		return []source{{kind: cciDir, id: id, lane: lane}}, start, true
	}
	return nil, 0, false
}

func (c Carrier) carry(ctx context.Context, named source) (string, error) {
	if named.kind == cciDir {
		return c.record(ctx, named)
	}
	if named.kind != "" {
		return c.note(ctx, named.kind, named.id)
	}
	if filepath.Clean(named.id) == filepath.Clean(c.Checkout) {
		return "", nil
	}
	if _, err := os.Lstat(named.id); named.variable && errors.Is(err, fs.ErrNotExist) {
		return c.Box + named.id, nil
	}
	return c.file(named.id)
}

func (c Carrier) note(ctx context.Context, kind, id string) (string, error) {
	shown, err := c.Runner.Run(ctx, shell.Command{Argv: []string{"ccn", "-R", c.Repo, kind, "show", id}})
	if err != nil {
		return "", fmt.Errorf("`ccn %s show %s` failed: %w", kind, id, err)
	}
	name := path.Join(notesDir, kind+"-"+id+".md")
	if err := write(filepath.Join(c.Into, name), []byte(shown.Stdout), 0o600); err != nil {
		return "", err
	}
	return path.Join(c.Box, name), nil
}

func (c Carrier) show(ctx context.Context, id string, flags ...string) (string, error) {
	argv := []string{"cci", "show"}
	if c.Drive != "" {
		argv = append(argv, "--drive", c.Drive)
	}
	shown, err := c.Runner.Run(ctx, shell.Command{Argv: append(append(argv, flags...), id), Idempotent: true})
	if err != nil {
		return "", fmt.Errorf("`cci show %s` failed: %w", id, err)
	}
	return shown.Stdout, nil
}

func (c Carrier) record(ctx context.Context, named source) (string, error) {
	if named.lane != "" {
		header, err := c.show(ctx, named.id, "--json")
		if err != nil {
			return "", nil
		}
		var cited record
		if err := json.Unmarshal([]byte(strings.SplitN(header, "\n", 2)[0]), &cited); err != nil {
			return "", fmt.Errorf("`cci show --json %s` printed no record: %w", named.id, err)
		}
		if !strings.EqualFold(cited.Lane, named.lane) && !slices.ContainsFunc(cited.To, func(to string) bool { return strings.EqualFold(to, named.lane) }) {
			return "", nil
		}
	}
	shown, err := c.show(ctx, named.id)
	if err != nil {
		return "", err
	}
	name := path.Join(cciDir, "record-"+named.id+".md")
	if err := write(filepath.Join(c.Into, name), []byte(shown), 0o600); err != nil {
		return "", err
	}
	return path.Join(c.Box, name), nil
}

func (c Carrier) file(name string) (string, error) {
	resolved, err := filepath.EvalSymlinks(name)
	if errors.Is(err, fs.ErrNotExist) {
		return "", fmt.Errorf("%s does not exist on this Mac", name)
	}
	if err != nil {
		return "", err
	}
	if !c.carries(resolved) {
		return "", fmt.Errorf("%s lies outside the drive's state dir and the scratch, plan, handoff and plugin-cache directories a launch carries", name)
	}
	var size int64
	var entries []string
	err = filepath.WalkDir(resolved, func(walked string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if !entry.IsDir() && !entry.Type().IsRegular() {
			return fmt.Errorf("%s holds %s, which is neither a file nor a directory", name, walked)
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		size += info.Size()
		entries = append(entries, walked)
		return nil
	})
	if err != nil {
		return "", err
	}
	if size > maxBytes {
		return "", fmt.Errorf("%s holds %d MiB, over the %d MiB a launch carries", name, (size+1<<20-1)>>20, maxBytes>>20)
	}
	for _, entry := range entries {
		if err := copyEntry(entry, filepath.Join(c.Into, name, strings.TrimPrefix(entry, resolved))); err != nil {
			return "", err
		}
	}
	return c.Box + name, nil
}

func (c Carrier) carries(resolved string) bool {
	roots := make([]string, 0, 1+len(carriedRoots))
	roots = append(roots, c.Vars[DriveDir])
	for _, root := range carriedRoots {
		roots = append(roots, filepath.Join(c.Home, root))
	}
	return slices.ContainsFunc(roots, func(root string) bool {
		if root == "" {
			return false
		}
		if linked, err := filepath.EvalSymlinks(root); err == nil {
			root = linked
		}
		return resolved == root || strings.HasPrefix(resolved, root+"/")
	})
}

func copyEntry(from, to string) error {
	info, err := os.Stat(from)
	if err != nil {
		return err
	}
	if info.IsDir() {
		return os.MkdirAll(to, 0o700)
	}
	data, err := os.ReadFile(from)
	if err != nil {
		return err
	}
	return write(to, data, info.Mode().Perm())
}

func write(to string, data []byte, mode fs.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(to), 0o700); err != nil {
		return err
	}
	return os.WriteFile(to, data, mode)
}
