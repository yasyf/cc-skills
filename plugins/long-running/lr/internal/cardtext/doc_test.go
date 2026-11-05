package cardtext

import (
	"strings"
	"testing"
)

func TestADocMergeAskCarriesTheDocsRenderedPage(t *testing.T) {
	const merge = "SandSQL sharding doc, revision 4: merge it?\nRevision 4 is on [design-docs#332](https://github.com/Forge-AI/design-docs/pull/332) with green checks.\nMerge the doc :: The lane merges revision 4."
	for name, tc := range map[string]struct {
		asked   string
		refused bool
	}{
		"a doc merge ask with only its PR link":   {merge, true},
		"a retro approval ask naming its PR":      {"Approve design-docs#340, the 4ntoya retro?", true},
		"a doc merge ask with its Pages link":     {merge + "\nRead it at https://docs.poetic.design/design-docs/sandsql-per-database-sharding/", false},
		"a doc merge ask with a github.io link":   {merge + "\nhttps://forge-ai.github.io/design-docs/sandsql-per-database-sharding/", false},
		"a doc merge ask with a tailnet link":     {merge + "\nhttp://yasyf-forge-studio.tail71af5d.ts.net:61713/design-docs/sandsql-per-database-sharding/", true},
		"a question about a doc, merging nothing": {"Which way does design-docs#332 shard, by table or by run?", false},
		"a code PR merge ask":                     {"Merge [#51418](https://github.com/Forge-AI/monorepo/pull/51418)?", false},
	} {
		t.Run(name, func(t *testing.T) {
			err := UnshownDoc(tc.asked)
			if tc.refused != (err != nil) || tc.refused && !strings.HasPrefix(err.Error(), "NO DOC PAGE") {
				t.Fatalf("refusal = %v, want refused %v", err, tc.refused)
			}
		})
	}
}
