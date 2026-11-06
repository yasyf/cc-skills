package findings

import (
	"encoding/json"
	"fmt"
	"io"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

const rowTime = "Jan 2 3:04 PM"

func Command(a *app.App) *cobra.Command {
	var name string
	cmd := &cobra.Command{
		Use:   "findings",
		Short: "Every sweep finding and owner pick, live until a record shows it carried out",
		Long: `A finding stays live until a later head or landed record names its PR or seq.
A pick also ends on a release, a claim or root's decide naming its card, its box
id or its seq, on a PR merged after it whose body or branch names one of them as
a whole word, and on a record whose delivered=owner:<row> attr names the ledger
row its pick came from. An opened record, or an answer or resolve of the row, marks
it routed, which is still live. The supervisor syncs on its tick.`,
	}
	cmd.PersistentFlags().StringVar(&name, "drive", "", "drive to read (default: the drive bound to this session)")
	book := func() (Book, error) {
		entry, err := drive.Named(name)
		if err != nil {
			return Book{}, err
		}
		return New(a, entry), nil
	}

	var sweep, owner, verdict, pr, line string
	post := &cobra.Command{Use: "post TEXT", Short: "Post one finding as a [finding:<sweep>] defect to its owner lane", Args: cobra.ExactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		b, err := book()
		if err != nil {
			return err
		}
		posted, err := b.Post(cmd.Context(), sweep, owner, verdict, pr, line, args[0])
		if err != nil {
			return err
		}
		_, err = fmt.Fprintln(cmd.OutOrStdout(), posted.Name())
		return err
	}}
	post.Flags().StringVar(&sweep, "sweep", "", "the lane that found it")
	post.Flags().StringVar(&owner, "owner", "", "the lane that owns the fix")
	post.Flags().StringVar(&verdict, "verdict", "", "one word")
	post.Flags().StringVar(&pr, "pr", "", "the PR it is about")
	post.Flags().StringVar(&line, "line", "", "path:line")
	for _, flag := range []string{"sweep", "owner", "verdict"} {
		if err := post.MarkFlagRequired(flag); err != nil {
			panic(err)
		}
	}

	var since string
	sync := &cobra.Command{Use: "sync", Short: "Add new findings and picks to the ledger and settle the live ones", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		b, err := book()
		if err != nil {
			return err
		}
		_, err = b.Sync(cmd.Context(), since)
		return err
	}}
	sync.Flags().StringVar(&since, "since", "", "read records from this seq or duration instead of the saved cursor; settles only, opening no row at or before the cursor")

	var openSweep, openPR string
	var asJSON bool
	shown := &cobra.Command{Use: "open", Short: "Sync, then print the open and routed rows by sweep", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		b, err := book()
		if err != nil {
			return err
		}
		rows, err := b.Sync(cmd.Context(), "")
		if err != nil {
			return err
		}
		return printRows(cmd.OutOrStdout(), Live(rows, openSweep, openPR), asJSON)
	}}
	shown.Flags().StringVar(&openSweep, "sweep", "", "only this sweep")
	shown.Flags().StringVar(&openPR, "pr", "", "only this PR")
	shown.Flags().BoolVar(&asJSON, "json", false, "one JSON row per line")

	show := &cobra.Command{Use: "show SEQ...", Short: "Sync, then print each named row whole, in any state, as one JSON row per line", Args: cobra.MinimumNArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		b, seqs, err := withSeqs(book, args)
		if err != nil {
			return err
		}
		rows, err := b.Sync(cmd.Context(), "")
		if err != nil {
			return err
		}
		named, err := Named(rows, seqs)
		if err != nil {
			return err
		}
		return printRows(cmd.OutOrStdout(), named, true)
	}}

	track := &cobra.Command{Use: "track SEQ...", Short: "Add the named records to the ledger", Args: cobra.MinimumNArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		b, seqs, err := withSeqs(book, args)
		if err != nil {
			return err
		}
		return b.Track(cmd.Context(), seqs)
	}}

	var why string
	closed := &cobra.Command{Use: "close --why TEXT SEQ...", Short: "Close rows no record will name, with the reason", Args: cobra.MinimumNArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		b, seqs, err := withSeqs(book, args)
		if err != nil {
			return err
		}
		return b.Close(cmd.Context(), why, seqs)
	}}
	closed.Flags().StringVar(&why, "why", "", "why no record will name it")
	if err := closed.MarkFlagRequired("why"); err != nil {
		panic(err)
	}

	citing := &cobra.Command{Use: "cited SEQ=BOX...", Short: "Print the seq of each pick a record of any kind, from anyone but board-watch, has named since: its card, box id or seq", Args: cobra.MinimumNArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		b, err := book()
		if err != nil {
			return err
		}
		picks := make(map[int64]string, len(args))
		for _, arg := range args {
			word, box, ok := strings.Cut(arg, "=")
			seq, err := strconv.ParseInt(word, 10, 64)
			if !ok || err != nil {
				return fmt.Errorf("pick %q is not SEQ=BOX", arg)
			}
			picks[seq] = box
		}
		found, err := b.Cited(cmd.Context(), picks)
		if err != nil {
			return err
		}
		for _, seq := range found {
			if _, err := fmt.Fprintln(cmd.OutOrStdout(), seq); err != nil {
				return err
			}
		}
		return nil
	}}

	cmd.AddCommand(post, sync, shown, show, track, closed, citing)
	return cmd
}

func withSeqs(book func() (Book, error), args []string) (Book, []int64, error) {
	b, err := book()
	if err != nil {
		return Book{}, nil, err
	}
	seqs := make([]int64, len(args))
	for i, arg := range args {
		if seqs[i], err = strconv.ParseInt(arg, 10, 64); err != nil {
			return Book{}, nil, fmt.Errorf("seq %q: %w", arg, err)
		}
	}
	return b, seqs, nil
}

func printRows(out io.Writer, rows []Fields, asJSON bool) error {
	pacific, err := time.LoadLocation("America/Los_Angeles")
	if err != nil {
		return err
	}
	sweep := ""
	for _, row := range rows {
		if asJSON {
			line, err := json.Marshal(row)
			if err != nil {
				return err
			}
			if _, err := fmt.Fprintln(out, string(line)); err != nil {
				return err
			}
			continue
		}
		if row["sweep"] != sweep {
			sweep = row["sweep"]
			if _, err := fmt.Fprintf(out, "%s:\n", sweep); err != nil {
				return err
			}
		}
		at, err := time.Parse(time.RFC3339Nano, row["at"])
		if err != nil {
			return fmt.Errorf("row #%s at: %w", row["seq"], err)
		}
		where := ""
		if row["pr"] != "" {
			where += "#" + row["pr"] + " "
		}
		if row["line"] != "" {
			where += row["line"] + " "
		}
		if _, err := fmt.Fprintf(out, "  #%s %s %s %sowner %s: %s\n", row["seq"], row["state"], at.In(pacific).Format(rowTime), where, row["owner"], row["text"]); err != nil {
			return err
		}
	}
	return nil
}
