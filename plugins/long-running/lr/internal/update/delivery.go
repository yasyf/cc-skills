package update

import (
	"cmp"
	"fmt"
	"slices"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

const (
	leadTitle   = "You asked more than once"
	askedAttr   = "asked"
	releaseAttr = "release"
	liveAttr    = "live"
	stillOpen   = "still open"
	askedAgain  = 2
)

func prNumber(key string) (int, bool) {
	number, err := strconv.Atoi(key[strings.LastIndex(key, "#")+1:])
	return number, err == nil
}

func recordPRs(record Record) []int {
	var numbers []int
	for _, key := range record.PRs {
		if number, ok := prNumber(key); ok {
			numbers = append(numbers, number)
		}
	}
	return numbers
}

func deliveryLine(prs []int, book asks.Book, released map[int]string) string {
	var phrases []string
	numbers := map[string][]string{}
	for _, number := range prs {
		pr, found := book.PRs[strconv.Itoa(number)]
		if !found || pr.Lane == "" {
			continue
		}
		phrase := stillOpen
		if !pr.Open() {
			if !pr.Landed() {
				continue
			}
			if phrase = released[number]; phrase == "" {
				return ""
			}
		}
		if _, seen := numbers[phrase]; !seen {
			phrases = append(phrases, phrase)
		}
		numbers[phrase] = append(numbers[phrase], "#"+strconv.Itoa(number))
	}
	parts := make([]string, len(phrases))
	for i, phrase := range phrases {
		parts[i] = strings.Join(numbers[phrase], " ") + " " + phrase
	}
	return strings.Join(parts, "; ")
}

func landedSHAs(pins []drive.Pin, leading []Record, book asks.Book) map[int]string {
	var named []int
	for _, pin := range pins {
		named = append(named, pin.PRs...)
	}
	for _, record := range leading {
		named = append(named, recordPRs(record)...)
	}
	shas := map[int]string{}
	for _, number := range named {
		pr, found := book.PRs[strconv.Itoa(number)]
		if found && pr.Lane != "" && pr.Landed() && pr.LandedSHA != "" {
			shas[number] = pr.LandedSHA
		}
	}
	return shas
}

func leadingAsks(records []Record) []Record {
	ordered := bySeq(records)
	cleared := map[int64]bool{}
	for _, record := range ordered {
		stated := record.Attrs[releaseAttr] != "" && record.Attrs[liveAttr] != ""
		if !stated && record.Attrs[askedAttr] != "0" {
			continue
		}
		cleared[record.Re] = true
		for _, seq := range record.Resolves {
			cleared[seq] = true
		}
	}
	newest := map[string]Record{}
	var keys []string
	for _, record := range ordered {
		times, err := strconv.Atoi(record.Attrs[askedAttr])
		if err != nil || times < askedAgain {
			continue
		}
		key := cmp.Or(record.Topic, strconv.FormatInt(record.Seq, 10))
		if _, seen := newest[key]; !seen {
			keys = append(keys, key)
		}
		newest[key] = record
	}
	var leading []Record
	for _, key := range keys {
		if !cleared[newest[key].Seq] {
			leading = append(leading, newest[key])
		}
	}
	return leading
}

func leadRows(in Inputs) []Row {
	rows := make([]Row, len(in.Leading))
	for i, record := range in.Leading {
		rows[i] = Row{
			Name:     fmt.Sprintf("Asked %s times: %s", record.Attrs[askedAttr], cmp.Or(record.Topic, opening(record.Text))),
			Delivers: clip(record.Text),
			State:    cmp.Or(deliveryLine(recordPRs(record), in.Book, in.Released), fmt.Sprintf("no release read for it; see %s", record.Name())),
			Who:      record.Lane,
			Blocks:   nothingBlocks,
			Unblock:  fmt.Sprintf("a record with --re %s --attr release=<n> --attr live=<time>", strings.TrimPrefix(record.Name(), "#")),
			Wrong:    noneRecorded,
		}
	}
	return rows
}

func leadLines(rows []Row) []string {
	if len(rows) == 0 {
		return nil
	}
	return titled(leadTitle, table(rows))
}

func withPin(pins []drive.Pin, pin drive.Pin) []drive.Pin {
	at := slices.IndexFunc(pins, func(existing drive.Pin) bool { return existing.Name == pin.Name })
	if at < 0 {
		return append(pins, pin)
	}
	if pin.Delivers != "" {
		pins[at].Delivers = pin.Delivers
	}
	if len(pin.Words) > 0 {
		pins[at].Words = pin.Words
	}
	if len(pin.PRs) > 0 {
		pins[at].PRs = pin.PRs
	}
	return pins
}

func pinCommand(driveName *string) *cobra.Command {
	var pin drive.Pin
	cmd := &cobra.Command{
		Use:   "pin NAME",
		Short: "Add or change a pinned row: what it delivers, the words that pick its lanes, and the PRs whose release it renders",
		Args:  cobra.ExactArgs(1),
		RunE: func(_ *cobra.Command, args []string) error {
			entry, err := drive.Named(*driveName)
			if err != nil {
				return err
			}
			pin.Name = args[0]
			entry.Pinned = withPin(entry.Pinned, pin)
			return drive.Save(entry)
		},
	}
	cmd.Flags().StringVar(&pin.Delivers, "delivers", "", "what the row delivers, in plain words")
	cmd.Flags().StringArrayVar(&pin.Words, "word", nil, "a word that picks the row's lanes by name, step or deliverable, repeatable; replaces the list")
	cmd.Flags().IntSliceVar(&pin.PRs, "pr", nil, "a PR the row delivers, repeatable; replaces the list")
	return cmd
}
