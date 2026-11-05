package coordinator

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
)

const (
	mutesFile  = "mutes.json"
	labelShown = 40
)

type mute struct {
	ID   int    `json:"id"`
	Kind string `json:"kind,omitempty"`
	Text string `json:"text,omitempty"`
	Why  string `json:"why"`

	pattern *regexp.Regexp
}

func (m mute) hides(r record) bool {
	if m.pattern != nil {
		return m.pattern.MatchString(line{r, 1}.head() + ": " + r.Text)
	}
	return r.Kind == m.Kind
}

func (m mute) label() string {
	if m.pattern == nil {
		return strings.ToUpper(m.Kind)
	}
	return "/" + cutLabel(m.Text) + "/"
}

func cutLabel(text string) string {
	if runes := []rune(text); len(runes) > labelShown {
		return string(runes[:labelShown]) + cutMark
	}
	return text
}

func loadMutes(path string) ([]mute, error) {
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var mutes []mute
	if err := json.Unmarshal(raw, &mutes); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	for i, m := range mutes {
		if m.Text == "" {
			continue
		}
		if mutes[i].pattern, err = regexp.Compile(m.Text); err != nil {
			return nil, fmt.Errorf("%s: mute %d: %w", path, m.ID, err)
		}
	}
	return mutes, nil
}

func saveMutes(path string, mutes []mute) error {
	raw, err := json.MarshalIndent(mutes, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	return os.WriteFile(path, append(raw, '\n'), 0o600)
}

func mutePath(driveName string) (string, error) {
	entry, err := drive.Named(driveName)
	if err != nil {
		return "", err
	}
	return filepath.Join(entry.StateDir, lanes.Root, mutesFile), nil
}

func muteCommand() *cobra.Command {
	var driveName string
	cmd := &cobra.Command{
		Use:   "mute",
		Short: "The kept list of records lr coordinator next hides, each counted in its muted line",
		Long: `The kept list of records lr coordinator next hides, kept beside its cursor.

A batch prints how many records each mute hid and where the whole batch is. No mute hides a hold, an OVERRIDE-WARNING, or a record that closes the window at once.`,
		Args: cobra.NoArgs,
	}
	cmd.PersistentFlags().StringVar(&driveName, "drive", "", "the drive whose list this is; default the drive this session belongs to")
	cmd.AddCommand(muteAddCommand(&driveName), muteListCommand(&driveName), muteRemoveCommand(&driveName))
	return cmd
}

func muteAddCommand(driveName *string) *cobra.Command {
	var kind, text, why string
	cmd := &cobra.Command{
		Use:   "add (--kind KIND | --text REGEXP) --why TEXT",
		Short: "Hide every record of one kind, or every record whose line matches a regexp",
		Long:  "--text matches the line as a batch prints it, with no body: `#SEQ 3:04 PM KIND lane re #N -> lanes: text`.",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if text != "" {
				if _, err := regexp.Compile(text); err != nil {
					return fmt.Errorf("--text: %w", err)
				}
			}
			path, err := mutePath(*driveName)
			if err != nil {
				return err
			}
			mutes, err := loadMutes(path)
			if err != nil {
				return err
			}
			added := mute{ID: nextMuteID(mutes), Kind: strings.ToLower(kind), Text: text, Why: why}
			if err := saveMutes(path, append(mutes, added)); err != nil {
				return err
			}
			_, err = fmt.Fprintln(cmd.OutOrStdout(), added.row())
			return err
		},
	}
	cmd.Flags().StringVar(&kind, "kind", "", "the record kind to hide")
	cmd.Flags().StringVar(&text, "text", "", "the regexp a record's line must match to hide")
	cmd.Flags().StringVar(&why, "why", "", "why root does not need these")
	cmd.MarkFlagsOneRequired("kind", "text")
	cmd.MarkFlagsMutuallyExclusive("kind", "text")
	_ = cmd.MarkFlagRequired("why")
	return cmd
}

func nextMuteID(mutes []mute) int {
	id := 1
	for _, m := range mutes {
		id = max(id, m.ID+1)
	}
	return id
}

func (m mute) row() string {
	if m.Text != "" {
		return fmt.Sprintf("%d text %s: %s", m.ID, m.Text, m.Why)
	}
	return fmt.Sprintf("%d kind %s: %s", m.ID, m.Kind, m.Why)
}

func muteListCommand(driveName *string) *cobra.Command {
	return &cobra.Command{
		Use:   "ls",
		Short: "Print each mute's id, match and reason",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			path, err := mutePath(*driveName)
			if err != nil {
				return err
			}
			mutes, err := loadMutes(path)
			if err != nil {
				return err
			}
			for _, m := range mutes {
				if _, err := fmt.Fprintln(cmd.OutOrStdout(), m.row()); err != nil {
					return err
				}
			}
			return nil
		},
	}
}

func muteRemoveCommand(driveName *string) *cobra.Command {
	return &cobra.Command{
		Use:   "rm ID",
		Short: "Stop hiding what one mute hides",
		Args:  cobra.ExactArgs(1),
		RunE: func(_ *cobra.Command, args []string) error {
			id, err := strconv.Atoi(args[0])
			if err != nil {
				return fmt.Errorf("mute id: %w", err)
			}
			path, err := mutePath(*driveName)
			if err != nil {
				return err
			}
			mutes, err := loadMutes(path)
			if err != nil {
				return err
			}
			i := slices.IndexFunc(mutes, func(m mute) bool { return m.ID == id })
			if i < 0 {
				return fmt.Errorf("no mute %d; lr coordinator mute ls lists them", id)
			}
			return saveMutes(path, slices.Delete(mutes, i, i+1))
		},
	}
}
