package codexguard

import (
	"context"
	"path"
	"slices"
	"strings"

	"mvdan.cc/sh/v3/syntax"
)

const storeWriteRefusal = "A lane writes an SSM SecureString, a Secrets Manager secret or a keychain item only when the owner's words name that write. Post a blocker to root naming the store and name; root records his grant with `git config --global --add long-running.credentialWrite <lane>=<store>:<name>`."

var (
	grantsRead     = []string{"git", "config", "--global", "--get-all", "long-running.credentialWrite"}
	notACredential = []string{"String", "StringList"}
	awsWrites      = map[string][]string{
		"ssm":            {"put-parameter", "delete-parameter", "delete-parameters"},
		"secretsmanager": {"create-secret", "put-secret-value", "update-secret", "delete-secret", "rotate-secret"},
	}
	awsSwitches = []string{
		"--debug", "--no-verify-ssl", "--no-paginate", "--no-sign-request", "--no-cli-pager", "--cli-auto-prompt", "--no-cli-auto-prompt", "--overwrite", "--no-overwrite",
		"--force-overwrite-replica-secret", "--no-force-overwrite-replica-secret", "--force-delete-without-recovery", "--no-force-delete-without-recovery", "--rotate-immediately", "--no-rotate-immediately",
	}
	keychainWrites       = []string{"add-generic-password", "add-internet-password", "delete-generic-password", "delete-internet-password"}
	keychainValueLetters = "acCdDGjlpPrstTwX"
)

type credentialWrite struct {
	store string
	names []string
}

func (g Guard) writesACredential(ctx context.Context, line, cwd, lane string) bool {
	writes := credentialWrites(line)
	if len(writes) == 0 {
		return false
	}
	granted := grantsTo(lane, strings.Split(g.read(ctx, grantsRead, cwd), "\n"))
	return slices.ContainsFunc(writes, func(write credentialWrite) bool {
		return len(write.names) == 0 || slices.ContainsFunc(write.names, func(name string) bool { return name == "" || !slices.Contains(granted, write.store+":"+name) })
	})
}

func credentialWrites(line string) []credentialWrite {
	file, err := syntax.NewParser().Parse(strings.NewReader(line), "")
	if err != nil {
		return nil
	}
	var writes []credentialWrite
	syntax.Walk(file, func(node syntax.Node) bool {
		call, ok := node.(*syntax.CallExpr)
		if !ok {
			return true
		}
		_, words := unwrapped(call)
		for i, part := range call.Args[len(call.Args)-len(words):] {
			if !exact(part) {
				words[i] = ""
			}
		}
		if payload, ok := shellPayload(words); ok {
			writes = append(writes, credentialWrites(payload)...)
		} else if write, ok := writeOf(words); ok {
			writes = append(writes, write)
		}
		return true
	})
	return writes
}

func writeOf(words []string) (credentialWrite, bool) {
	if len(words) < 2 {
		return credentialWrite{}, false
	}
	switch path.Base(words[0]) {
	case "aws":
		positional, values := parseArgs(words[1:], awsSwitches, "")
		if len(positional) < 2 || !slices.Contains(awsWrites[positional[0]], positional[1]) {
			return credentialWrite{}, false
		}
		if types := values["--type"]; len(types) > 0 && slices.Contains(notACredential, types[len(types)-1]) {
			return credentialWrite{}, false
		}
		names := slices.Concat(values["--name"], values["--secret-id"], values["--names"])
		if positional[1] == "delete-parameters" {
			names = append(names, positional[2:]...)
		}
		return credentialWrite{store: positional[0], names: names}, true
	case "security":
		positional, values := parseArgs(words[1:], nil, keychainValueLetters)
		if len(positional) == 0 || !slices.Contains(keychainWrites, positional[0]) {
			return credentialWrite{}, false
		}
		return credentialWrite{store: "keychain", names: values["-s"]}, true
	}
	return credentialWrite{}, false
}

func parseArgs(args, longSwitches []string, shortValues string) ([]string, map[string][]string) {
	var positional []string
	values := map[string][]string{}
	for i := 0; i < len(args); i++ {
		word := args[i]
		switch {
		case strings.HasPrefix(word, "--"):
			flag, inline, attached := strings.Cut(word, "=")
			switch {
			case attached:
				values[flag] = append(values[flag], inline)
			case slices.Contains(longSwitches, flag):
			case i+1 < len(args):
				i++
				values[flag] = append(values[flag], args[i])
			}
		case len(word) > 1 && word[0] == '-':
			flag := word[:2]
			switch {
			case !strings.Contains(shortValues, word[1:2]):
			case len(word) > 2:
				values[flag] = append(values[flag], word[2:])
			case i+1 < len(args):
				i++
				values[flag] = append(values[flag], args[i])
			}
		default:
			positional = append(positional, word)
		}
	}
	return positional, values
}

func exact(word *syntax.Word) bool {
	return !slices.ContainsFunc(word.Parts, func(part syntax.WordPart) bool {
		switch piece := part.(type) {
		case *syntax.Lit, *syntax.SglQuoted:
			return false
		case *syntax.DblQuoted:
			return slices.ContainsFunc(piece.Parts, func(inner syntax.WordPart) bool {
				_, ok := inner.(*syntax.Lit)
				return !ok
			})
		}
		return true
	})
}
