package carry

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"maps"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"slices"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	maxBytes = 64 << 20
	notesDir = "ccn"
	pathStop = `\s` + "`" + `'"<>()\[\]{},;:|*?$`
	template = "<{$*"
	joined   = `\w./~-`

	DriveDir = "DRIVE_DIR"
)

var (
	reference   = regexp.MustCompile(`ccn (doc|plan|task) show ([0-9A-Za-z][\w-]*)|\$\{?([A-Z_][A-Z0-9_]*)\}?/[^` + pathStop + `]+|(?:~/|/)[^` + pathStop + `]+`)
	assigned    = regexp.MustCompile(`(?m)(?:^|[\s;` + "`" + `])(?:export )?([A-Z_][A-Z0-9_]*)=["']?((?:~/|/)[^` + pathStop + `]+)`)
	joinedByte  = regexp.MustCompile(`[` + joined + `]`)
	credentials = []string{".ssh", ".aws", ".gnupg", ".config/gh", ".netrc"}
)

type source struct {
	kind, id string
	variable bool
}

type Carrier struct {
	Runner shell.Runner
	Home   string
	Repo   string
	Into   string
	Box    string
	Vars   map[string]string
}

func Command(a *app.App) *cobra.Command {
	c := Carrier{Runner: a.Runner, Vars: map[string]string{}}
	cmd := &cobra.Command{
		Use:   "carry BRIEF",
		Short: "Copy each file under this home and each ccn doc, plan or task a brief names into --into, which lands at --box on a Sprite, and print the brief with every reference rewritten to its copy",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			brief, err := os.ReadFile(args[0])
			if err != nil {
				return err
			}
			if c.Home, err = os.UserHomeDir(); err != nil {
				return err
			}
			if config, err := a.ConfigFile(); err == nil {
				c.Vars[DriveDir] = filepath.Dir(config)
			}
			rewritten, err := c.Carry(cmd.Context(), string(brief))
			if err != nil {
				return fmt.Errorf("brief %s names what no Sprite can receive: %w", args[0], err)
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

func (c Carrier) Carry(ctx context.Context, brief string) (string, error) {
	var rewritten strings.Builder
	copies := map[source]string{}
	var faults []string
	last := 0
	vars := c.varsOf(brief)
	for _, match := range reference.FindAllStringSubmatchIndex(brief, -1) {
		named, end, ok := c.named(brief, match, vars)
		if !ok {
			continue
		}
		copied, seen := copies[named]
		if !seen {
			var err error
			if copied, err = c.carry(ctx, named); err != nil {
				faults = append(faults, err.Error())
			}
			copies[named] = copied
		}
		rewritten.WriteString(brief[last:match[0]] + copied)
		last = end
	}
	if len(faults) > 0 {
		return "", errors.New(strings.Join(faults, "; "))
	}
	return rewritten.String() + brief[last:], nil
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

func (c Carrier) named(brief string, match []int, vars map[string]string) (source, int, bool) {
	if match[2] >= 0 {
		return source{kind: brief[match[2]:match[3]], id: brief[match[4]:match[5]]}, match[1], true
	}
	start, end := match[0], match[1]
	token := brief[start:end]
	if start > 0 && joinedByte.MatchString(brief[start-1:start]) || end < len(brief) && strings.ContainsRune(template, rune(brief[end])) {
		return source{}, 0, false
	}
	if strings.Contains(token, "…") || strings.HasSuffix(token, "..") {
		return source{}, 0, false
	}
	token = strings.TrimSuffix(token, ".")
	name := token
	variable := match[6] >= 0
	if variable {
		value, known := vars[brief[match[6]:match[7]]]
		if !known {
			return source{}, 0, false
		}
		name = value + token[strings.Index(token, "/"):]
	} else if rest, ok := strings.CutPrefix(token, "~/"); ok {
		name = c.Home + "/" + rest
	}
	if !strings.HasPrefix(name, c.Home+"/") || name == c.Home+"/" {
		return source{}, 0, false
	}
	return source{id: name, variable: variable}, start + len(token), true
}

func (c Carrier) carry(ctx context.Context, named source) (string, error) {
	if named.kind != "" {
		return c.note(ctx, named.kind, named.id)
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

func (c Carrier) file(name string) (string, error) {
	if c.credential(name) {
		return "", fmt.Errorf("%s is a credential path, which never leaves this Mac", name)
	}
	resolved, err := filepath.EvalSymlinks(name)
	if errors.Is(err, fs.ErrNotExist) {
		return "", fmt.Errorf("%s does not exist on this Mac", name)
	}
	if err != nil {
		return "", err
	}
	if c.credential(resolved) {
		return "", fmt.Errorf("%s links to %s, a credential path, which never leaves this Mac", name, resolved)
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

func (c Carrier) credential(name string) bool {
	rel, ok := strings.CutPrefix(name, c.Home+"/")
	return ok && slices.ContainsFunc(credentials, func(dir string) bool { return rel == dir || strings.HasPrefix(rel, dir+"/") })
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
