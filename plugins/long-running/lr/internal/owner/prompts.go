package owner

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

func promptsCommand(driveName *string) *cobra.Command {
	var since string
	cmd := &cobra.Command{
		Use:   "prompts [--since DIGEST]",
		Short: "Print the owner's captures in the drive's " + capturesFile + " as stored, one JSON line each, only those after the capture --since names; a Sprite lane reads the Mac's file through the store desk",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			entry, err := drive.Named(*driveName)
			if err != nil {
				return err
			}
			stored, err := os.ReadFile(filepath.Join(entry.StateDir, capturesFile))
			if err != nil {
				return err
			}
			return capturesAfter(string(stored), since, cmd.OutOrStdout())
		},
	}
	cmd.Flags().StringVar(&since, "since", "", "the digest of the last capture already read")
	return cmd
}

func capturesAfter(stored, since string, out io.Writer) error {
	var after strings.Builder
	found := since == ""
	for line := range strings.Lines(stored) {
		if strings.TrimSpace(line) == "" {
			continue
		}
		if found {
			after.WriteString(line)
			continue
		}
		var capture slackCapture
		if err := json.Unmarshal([]byte(line), &capture); err != nil {
			return fmt.Errorf("%s: %w", capturesFile, err)
		}
		found = capture.Digest == since
	}
	if !found {
		return fmt.Errorf("%s holds no capture %s", capturesFile, since)
	}
	_, err := io.WriteString(out, after.String())
	return err
}
