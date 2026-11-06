package codexguard

import (
	"context"
	"path"
	"slices"
	"strings"

	"mvdan.cc/sh/v3/syntax"
)

const proofKey = "long-running.signedinproof="

const irisRefusal = "This test Mac's Iris is signed in as the owner, so launching it calls production as him, and only a released-build proof on his Personal team that root granted this lane may. Otherwise run the check on a fresh box as a test user on staging: `ci mac-box up <name>`."

var (
	boxesRead  = []string{"git", "config", "--global", "--get-all", "long-running.signedInBox"}
	configRead = []string{"git", "config", "--global", "--list"}
	irisApp    = []string{"Iris", "Iris.app"}
	sands      = []string{"sand", "sand-next"}

	launchctlRunners  = []string{"asuser", "bsexec"}
	wrapperValueFlags = map[string][]string{"sudo": {"-u", "-g"}, "env": {"-u"}}
)

func (g Guard) launchesIrisAsHim(ctx context.Context, line, cwd, lane string) bool {
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
	var launches []map[string]string
	for _, text := range remote {
		launches = append(launches, irisLaunches(text)...)
	}
	if len(reaching) == 0 || len(launches) == 0 {
		return false
	}
	boxes := strings.Fields(g.read(ctx, boxesRead, cwd))
	onBox := false
	for _, args := range reaching {
		for _, word := range args {
			onBox = onBox || slices.ContainsFunc(strings.Split(word, "/"), func(part string) bool { return slices.Contains(boxes, part) })
		}
	}
	if !onBox {
		return false
	}
	config := g.read(ctx, configRead, cwd)
	if config == "" {
		return false
	}
	proofs := proofsGranted(config, lane)
	return slices.ContainsFunc(launches, func(env map[string]string) bool {
		return env["IRIS_TEAM"] != "personal" || !slices.Contains(proofs, env["IRIS_RELEASE_PROOF"])
	})
}

func proofsGranted(config, lane string) []string {
	var grants []string
	for _, line := range strings.Split(config, "\n") {
		if grant, ok := strings.CutPrefix(line, proofKey); ok {
			grants = append(grants, grant)
		}
	}
	return grantsTo(lane, grants)
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

func irisLaunches(text string) []map[string]string {
	file, err := syntax.NewParser().Parse(strings.NewReader(text), "")
	if err != nil {
		return nil
	}
	var launches []map[string]string
	syntax.Walk(file, func(node syntax.Node) bool {
		call, ok := node.(*syntax.CallExpr)
		if !ok {
			return true
		}
		env, words := unwrapped(call)
		if len(words) == 0 {
			return true
		}
		name := path.Base(words[0])
		if name == "open" && slices.ContainsFunc(words[1:], func(arg string) bool { return slices.Contains(irisApp, path.Base(arg)) }) ||
			slices.Contains(strings.Split(words[0], "/"), "Iris.app") ||
			slices.Contains(sands, name) && len(words) > 1 && words[1] == "iris" && env["IRIS_PLANE"] != "staging" {
			launches = append(launches, env)
		}
		return true
	})
	return launches
}

func unwrapped(call *syntax.CallExpr) (map[string]string, []string) {
	env := map[string]string{}
	for _, assign := range call.Assigns {
		if assign.Name != nil && assign.Value != nil {
			env[assign.Name.Value] = literal(assign.Value)
		}
	}
	words := make([]string, 0, len(call.Args))
	for _, part := range call.Args {
		words = append(words, literal(part))
	}
	for len(words) > 0 {
		wrapper := path.Base(words[0])
		switch {
		case wrapper == "cc-sudo" && len(words) > 2 && words[1] == "run" && words[2] == "--",
			wrapper == "launchctl" && len(words) > 2 && slices.Contains(launchctlRunners, words[1]):
			words = words[3:]
		case slices.Contains(wrappers, wrapper):
			words = words[1:]
			for len(words) > 0 && (strings.HasPrefix(words[0], "-") || strings.Contains(words[0], "=")) {
				if name, value, ok := strings.Cut(words[0], "="); ok {
					env[name] = value
				}
				if slices.Contains(wrapperValueFlags[wrapper], words[0]) && len(words) > 1 {
					words = words[1:]
				}
				words = words[1:]
			}
		default:
			return env, words
		}
	}
	return env, words
}
