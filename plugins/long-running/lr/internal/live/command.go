package live

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	configFile     = "lr.json"
	answerLabel    = "board-answer"
	boardCardLabel = "board-card:"
	pickedLead     = "picked “"
	pickedEnd      = "”"
)

type readRequest struct {
	Bindings []Binding `json:"bindings"`
	Cards    []Card    `json:"cards"`
}

func Command(a *app.App) *cobra.Command {
	var driveName string
	cmd := &cobra.Command{Use: "live", Short: "Read what a card's bound blocks show, from their sources"}
	cmd.PersistentFlags().StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	cmd.AddCommand(&cobra.Command{
		Use:   "read",
		Short: `Read {"bindings": [...], "cards": [...]} on stdin and print one {"items", "total"} per binding, with one read per source`,
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			var request readRequest
			if err := json.NewDecoder(c.InOrStdin()).Decode(&request); err != nil {
				return fmt.Errorf("read the bindings: %w", err)
			}
			entry, err := drive.Named(driveName)
			if err != nil {
				return err
			}
			cfg, err := config.Load(filepath.Join(entry.StateDir, configFile), io.Discard)
			if err != nil {
				return err
			}
			s, err := store.Open(entry.Store())
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			results, err := Read(Sources{
				Repo: cfg.Repo, Cards: request.Cards, Now: a.Now, Rows: s.ReviewRows,
				Book: func() (asks.Book, error) {
					mirror, err := asks.OpenMirror(a, entry)
					if err != nil {
						return asks.Book{}, err
					}
					defer func() { _ = mirror.Store.Close() }()
					return mirror.Book(c.Context())
				},
				LeftBoard: func(ids []string) (map[string]Card, error) {
					return leftBoard(c.Context(), a.Runner, entry.Checkout, ids)
				},
			}, request.Bindings)
			if err != nil {
				return err
			}
			return json.NewEncoder(c.OutOrStdout()).Encode(results)
		},
	})
	return cmd
}

func leftBoard(ctx context.Context, runner shell.Runner, checkout string, ids []string) (map[string]Card, error) {
	done, err := runner.Run(ctx, shell.Command{Argv: []string{"ccn", "-R", checkout, "answer", "list", "--label", answerLabel, "--limit", "0", "--full", "--json"}})
	if err != nil {
		return nil, err
	}
	var answers []struct {
		Title string   `json:"title"`
		Body  string   `json:"body"`
		Tags  []string `json:"tags"`
	}
	if strings.TrimSpace(done.Stdout) != "" {
		if err := json.Unmarshal([]byte(done.Stdout), &answers); err != nil {
			return nil, fmt.Errorf("ccn answer list: %w", err)
		}
	}
	left := map[string]Card{}
	for _, answer := range answers {
		for _, tag := range answer.Tags {
			id, isCard := strings.CutPrefix(tag, boardCardLabel)
			if isCard && slices.Contains(ids, id) {
				left[id] = Card{ID: id, Title: answer.Title, Picked: picked(answer.Body)}
			}
		}
	}
	return left, nil
}

func picked(body string) string {
	first, _, _ := strings.Cut(body, "\n")
	words, _ := strings.CutPrefix(first, pickedLead)
	return strings.TrimSuffix(words, pickedEnd)
}
