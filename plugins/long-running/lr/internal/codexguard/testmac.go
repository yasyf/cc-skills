package codexguard

import (
	"context"
	"path"
	"slices"
	"strings"

	"mvdan.cc/sh/v3/syntax"
)

const irisRefusal = "This test Mac's Iris is signed in as the owner, so launching it calls production as him, and no lane, root or desk can allow that. Run the check on a fresh box signed in as a test user on staging: `ci mac-box up <name>`."

var (
	boxesRead = []string{"git", "config", "--global", "--get-all", "long-running.signedInBox"}
	irisApp   = []string{"Iris", "Iris.app"}
	sands     = []string{"sand", "sand-next"}
)

func (g Guard) launchesIrisAsHim(ctx context.Context, line, cwd string) bool {
	file, err := syntax.NewParser().Parse(strings.NewReader(line), "")
	if err != nil {
		return false
	}
	var reaching [][]string
	var remote []string
	syntax.Walk(file, func(node syntax.Node) bool {
		stmt, ok := node.(*syntax.Stmt)
		if !ok {
			return true
		}
		call, ok := stmt.Cmd.(*syntax.CallExpr)
		if !ok {
			return true
		}
		args := boxArgs(call)
		if args == nil {
			return true
		}
		reaching = append(reaching, args)
		remote = append(remote, remoteTexts(args)...)
		for _, redir := range stmt.Redirs {
			if redir.Hdoc != nil {
				remote = append(remote, literal(redir.Hdoc))
			}
		}
		return true
	})
	if len(reaching) == 0 || !slices.ContainsFunc(remote, launchesIris) {
		return false
	}
	boxes := strings.Fields(g.read(ctx, boxesRead, cwd))
	for _, args := range reaching {
		for _, word := range args {
			if slices.ContainsFunc(strings.Split(word, "/"), func(part string) bool { return slices.Contains(boxes, part) }) {
				return true
			}
		}
	}
	return false
}

func boxArgs(call *syntax.CallExpr) []string {
	_, words := unwrapped(call)
	switch {
	case len(words) > 3 && path.Base(words[0]) == "ci" && words[1] == "mac-box" && words[2] == "ssh":
		return words[3:]
	case len(words) > 1 && path.Base(words[0]) == "ssh":
		return words[1:]
	}
	return nil
}

func remoteTexts(args []string) []string {
	tail := args[1:]
	if at := slices.Index(args, "--"); at >= 0 {
		tail = args[at+1:]
	}
	texts := []string{strings.Join(tail, " ")}
	for _, word := range args {
		if strings.Contains(word, " ") {
			texts = append(texts, word)
		}
	}
	return texts
}

func launchesIris(text string) bool {
	file, err := syntax.NewParser().Parse(strings.NewReader(text), "")
	if err != nil {
		return false
	}
	launches := false
	syntax.Walk(file, func(node syntax.Node) bool {
		call, ok := node.(*syntax.CallExpr)
		if !ok || launches {
			return !launches
		}
		plane, words := unwrapped(call)
		if len(words) == 0 {
			return true
		}
		name := path.Base(words[0])
		launches = name == "open" && slices.ContainsFunc(words[1:], func(arg string) bool { return slices.Contains(irisApp, path.Base(arg)) }) ||
			slices.Contains(strings.Split(words[0], "/"), "Iris.app") ||
			slices.Contains(sands, name) && len(words) > 1 && words[1] == "iris" && plane != "staging"
		return !launches
	})
	return launches
}

func unwrapped(call *syntax.CallExpr) (string, []string) {
	plane := ""
	for _, assign := range call.Assigns {
		if assign.Name != nil && assign.Name.Value == "IRIS_PLANE" && assign.Value != nil {
			plane = literal(assign.Value)
		}
	}
	words := make([]string, 0, len(call.Args))
	for _, part := range call.Args {
		words = append(words, literal(part))
	}
	for len(words) > 0 && slices.Contains(wrappers, path.Base(words[0])) {
		words = words[1:]
		for len(words) > 0 && (strings.HasPrefix(words[0], "-") || strings.Contains(words[0], "=")) {
			if value, ok := strings.CutPrefix(words[0], "IRIS_PLANE="); ok {
				plane = value
			}
			words = words[1:]
		}
	}
	return plane, words
}
