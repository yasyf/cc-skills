package lanes

import "testing"

func TestRegisteredNumberedNamesReadAsTheirAliases(t *testing.T) {
	aliases := AliasesOf([]string{"tap-rows-1", "s3-errors-12", "meta-desk", "owner-relay"})
	for _, tc := range []struct{ name, text, want string }{
		{"a bare name", "tap-rows-1 merged it", "tap-rows merged it"},
		{"the end of a sentence", "Ask s3-errors-12.", "Ask s3-errors."},
		{"code and a flag", "`cci post --to=tap-rows-1`", "`cci post --to=tap-rows`"},
		{"a list", "tap-rows-1,s3-errors-12", "tap-rows,s3-errors"},
		{"an unnumbered lane", "meta-desk and owner-relay", "meta-desk and owner-relay"},
		{"a name not registered", "tap-rows-2 and step-1", "tap-rows-2 and step-1"},
		{"a longer word", "dar-tap-rows-1 and tap-rows-1b and tap-rows-1.md", "dar-tap-rows-1 and tap-rows-1b and tap-rows-1.md"},
		{"a worktree path", "~/repos/claude/worktrees/x/tap-rows-1 and x/tap-rows-1", "~/repos/claude/worktrees/x/tap-rows-1 and x/tap-rows-1"},
		{"a link", "[row](https://example.com/q?lane=tap-rows-1) by tap-rows-1", "[row](https://example.com/q?lane=tap-rows-1) by tap-rows"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := aliases.In(tc.text); got != tc.want {
				t.Fatalf("In(%q) = %q, want %q", tc.text, got, tc.want)
			}
		})
	}
}
