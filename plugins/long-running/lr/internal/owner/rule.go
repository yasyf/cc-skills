package owner

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	ownerAnswer = "from:owner"
	answerTitle = "What did the owner say at %s?"
	answersDir  = "answers"
)

type answer struct {
	ID    string   `json:"id"`
	Title string   `json:"title"`
	Body  string   `json:"body"`
	Tags  []string `json:"tags"`
}

func ruleCommand(a *app.App, driveName *string) *cobra.Command {
	var reply string
	cmd := &cobra.Command{
		Use:   "rule ROW",
		Short: "Record an owner ruling in one call: his message's cc-notes answer labeled durable with its row, the reply as the row's answer file, and the row marked responded",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			entry, err := drive.Named(*driveName)
			if err != nil {
				return err
			}
			path, replies := Ledger(entry.StateDir), filepath.Join(entry.StateDir, repliesDir)
			ledger, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			rows := Parse(ledger)
			i := slices.IndexFunc(rows, func(r Row) bool { return r.ID == args[0] })
			if i < 0 {
				return fmt.Errorf("no row %s in %s", args[0], path)
			}
			row := rows[i]
			md, err := replyMD(replies, reply)
			if err != nil {
				return fmt.Errorf("%s: %w", row.ID, err)
			}
			if err := carried(path, replies, row.ID, reply); err != nil {
				return err
			}
			id, err := ruled(cmd.Context(), a.Runner, entry, row)
			if err != nil {
				return err
			}
			kept := filepath.Join(filepath.Dir(path), answersDir, row.ID+".md")
			if err := keep(kept, md); err != nil {
				return err
			}
			if err := Mark(path, row.ID, responded, 0); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "%s ruled: answer %s, %s, %s\n", row.ID, id, kept, responded)
			return nil
		},
	}
	cmd.Flags().StringVar(&reply, "reply", "", "the reply that answered the ruling")
	_ = cmd.MarkFlagRequired("reply")
	return cmd
}

func ruled(ctx context.Context, runner shell.Runner, entry drive.Entry, row Row) (string, error) {
	ccn := func(args ...string) (string, error) {
		result, err := runner.Run(ctx, shell.Command{Argv: append([]string{"ccn", "-R", entry.Checkout, "answer"}, args...)})
		return result.Stdout, err
	}
	labels := []string{"drive:" + entry.Bus(), "owner:" + row.ID, "scope:durable"}
	listed, err := ccn("list", "--label", ownerAnswer, "--limit", "0", "--json")
	if err != nil {
		return "", err
	}
	var found []answer
	if err := json.Unmarshal([]byte(listed), &found); err != nil {
		return "", fmt.Errorf("ccn answer list: %w", err)
	}
	title := fmt.Sprintf(answerTitle, row.At)
	i := slices.IndexFunc(found, func(a answer) bool { return slices.Contains(a.Tags, labels[1]) })
	if i < 0 {
		said := strings.Join(strings.Fields(row.Text), " ")
		i = slices.IndexFunc(found, func(a answer) bool {
			return a.Title == title && strings.Contains(strings.Join(strings.Fields(a.Body), " "), said)
		})
	}
	if i >= 0 {
		edit := []string{"edit", found[i].ID}
		for _, label := range labels {
			if !slices.Contains(found[i].Tags, label) {
				edit = append(edit, "--add-label", label)
			}
		}
		if len(edit) > 2 {
			_, err = ccn(edit...)
		}
		return found[i].ID, err
	}
	add := []string{"add", "--json", "--label", ownerAnswer}
	for _, label := range labels {
		add = append(add, "--label", label)
	}
	added, err := ccn(append(add, "--body", row.Text, "--", title)...)
	if err != nil {
		return "", err
	}
	var made answer
	if err := json.Unmarshal([]byte(added), &made); err != nil {
		return "", fmt.Errorf("ccn answer add: %w", err)
	}
	return made.ID, nil
}

func keep(path, md string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	held, err := os.ReadFile(path)
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	if len(held) >= len(md) {
		return nil
	}
	return os.WriteFile(path, []byte(md), 0o600)
}
