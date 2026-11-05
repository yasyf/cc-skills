package codexguard

import (
	"context"
	"encoding/json"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"slices"
	"strings"

	"mvdan.cc/sh/v3/syntax"
)

const (
	productionRefusal = "A production secret, pod or database read needs the owner's tap on that exact read. Ask him with `lr ask-owner` naming the parameter, pod or table, then rerun with `# ccx:owner-approval=<seq>` citing owner-relay's owner record."
	reachDepth        = 3
	kubeContextKey    = "long-running.productionkubecontext"
	kubeNamespaceKey  = "long-running.productionkubenamespace"
	dbHostKey         = "long-running.productiondbhost"
)

var (
	productionConfigRead = []string{"git", "config", "--global", "--get-regexp", `^long-running\.production`}
	currentContextRead   = []string{"kubectl", "config", "current-context"}
	ownerLanes           = []string{"owner", "owner-relay"}
	approvalComment      = regexp.MustCompile(`(?:^|\s)ccx:owner-approval=([0-9]+)(?:\s|$)`)
	tableName            = regexp.MustCompile(`(?i)\b(?:from|join)\s+([\w."]+)`)
	conninfoHost         = regexp.MustCompile(`(?:^|\s)host=(\S+)|^postgres(?:ql)?://(?:[^@/]*@)?([^:/?]+)`)
	dbReadPipeline       = regexp.MustCompile(`(?:^|/)db-read(?:/|$)`)
	dbRead               = []string{"db-read"}
	scriptSuffixes       = []string{".sh", ".bash", ".zsh"}
	sshValueFlags        = strings.Fields("-B -b -c -D -E -e -F -I -i -J -L -l -m -O -o -P -p -Q -R -S -W -w")
	spriteValueFlags     = []string{"-s", "--sprite", "-o", "--org"}
	awsValueFlags        = []string{"--profile", "--region", "--output", "--query", "--endpoint-url", "--color", "--cli-read-timeout", "--cli-connect-timeout", "--ca-bundle", "--cli-binary-format"}
	ssmReads             = []string{"get-parameter", "get-parameters", "get-parameters-by-path"}
	secretReads          = []string{"get-secret-value", "batch-get-secret-value"}
	keychainLookups      = []string{"find-generic-password", "find-internet-password"}
	opValueFlags         = []string{"--account", "--vault", "--fields", "--format", "--session", "--config", "--cache"}
	opReveals            = []string{"--reveal", "--fields"}
	kubectlValueFlags    = []string{"-n", "--namespace", "--context", "-c", "--container", "--kubeconfig", "--cluster", "--user", "-s", "--server", "--pod-running-timeout", "--address"}
	podVerbs             = []string{"exec", "cp", "port-forward"}
	bkValueFlags         = []string{"-X", "--method", "-p", "--pipeline", "-m", "--message", "-b", "--branch", "-c", "--commit", "-e", "--env", "-d", "--data", "-f", "--file"}
)

type reachedCall struct {
	words  []string
	env    map[string]string
	nested bool
}

type production struct {
	guard       Guard
	ctx         context.Context
	cwd         string
	config      map[string][]string
	kubeContext *string
}

func (g Guard) readsProductionUnapproved(ctx context.Context, line, cwd string) bool {
	file, err := syntax.NewParser(syntax.KeepComments(true)).Parse(strings.NewReader(line), "")
	if err != nil {
		return false
	}
	p := &production{guard: g, ctx: ctx, cwd: cwd}
	var named []string
	for _, call := range reach(file, cwd, false, 0) {
		named = append(named, targets(call, p)...)
	}
	return len(named) > 0 && !g.approved(ctx, approvalSeq(file), named, cwd)
}

func targets(call reachedCall, p *production) []string {
	if found := secretTargets(call.words); found != nil {
		return found
	}
	if found := podTargets(call, p); found != nil {
		return found
	}
	return dbTargets(call, p)
}

func secretTargets(words []string) []string {
	args := words[1:]
	switch path.Base(words[0]) {
	case "aws":
		service := operands(args, awsValueFlags)
		if len(service) > 1 && service[0] == "ssm" && slices.Contains(ssmReads, service[1]) && slices.Contains(args, "--with-decryption") {
			return namedOrWhole(values(args, "--name", "--names", "--path"), args)
		}
		if len(service) > 1 && service[0] == "secretsmanager" && slices.Contains(secretReads, service[1]) {
			return namedOrWhole(values(args, "--secret-id", "--secret-id-list"), args)
		}
	case "security":
		if len(args) > 0 && slices.Contains(keychainLookups, args[0]) && (slices.Contains(args, "-w") || slices.Contains(args, "-g")) {
			return namedOrWhole(first(values(args, "-s", "-l", "-a")), args)
		}
	case "op":
		said := operands(args, opValueFlags)
		if len(said) > 0 && said[0] == "read" {
			return namedOrWhole(first(said[1:]), args)
		}
		if len(said) > 1 && said[0] == "item" && said[1] == "get" && slices.ContainsFunc(args, reveals) {
			return namedOrWhole(first(said[2:]), args)
		}
	}
	return nil
}

func reveals(arg string) bool {
	flag, _, _ := strings.Cut(arg, "=")
	return slices.Contains(opReveals, flag)
}

func podTargets(call reachedCall, p *production) []string {
	if path.Base(call.words[0]) != "kubectl" {
		return nil
	}
	args := call.words[1:]
	words := operands(args, kubectlValueFlags)
	if len(words) == 0 || !slices.Contains(podVerbs, words[0]) {
		return nil
	}
	kubeContext := strings.Join(first(values(args, "--context")), "")
	if kubeContext == "" && !call.nested {
		kubeContext = p.currentContext()
	}
	if !p.names(kubeContextKey, kubeContext) && !p.names(kubeNamespaceKey, strings.Join(first(values(args, "-n", "--namespace")), "")) {
		return nil
	}
	var pods []string
	for _, word := range words[1:] {
		if words[0] == "cp" && !strings.Contains(word, ":") {
			continue
		}
		pod, _, _ := strings.Cut(word, ":")
		pods = append(pods, pod[strings.LastIndex(pod, "/")+1:])
	}
	return namedOrWhole(first(pods), args)
}

func dbTargets(call reachedCall, p *production) []string {
	name, args := path.Base(call.words[0]), call.words[1:]
	words := operands(args, bkValueFlags)
	switch {
	case name == "ci" && len(words) > 0 && words[0] == "db-read":
		return orElse(tables(values(args, "--sql")), dbRead)
	case (name == "ci" || name == "bk") && slices.Contains(words[:min(2, len(words))], "api") && slices.Contains(values(args, "-X", "--method"), "POST") && slices.ContainsFunc(words, dbReadPipeline.MatchString):
		return orElse(tables(args), dbRead)
	case name == "bk" && len(words) > 1 && words[0] == "build" && words[1] == "create" && slices.ContainsFunc(values(args, "-p", "--pipeline"), dbReadPipeline.MatchString):
		return orElse(tables(args), dbRead)
	case name != "psql":
		return nil
	}
	hosts := append(values(args, "-h", "--host"), call.env["PGHOST"])
	for _, arg := range args {
		for _, found := range conninfoHost.FindAllStringSubmatch(arg, -1) {
			hosts = append(hosts, found[1:]...)
		}
	}
	var productionHosts []string
	for _, host := range hosts {
		if p.names(dbHostKey, host) && !slices.Contains(productionHosts, host) {
			productionHosts = append(productionHosts, host)
		}
	}
	if productionHosts == nil {
		return nil
	}
	slices.Sort(productionHosts)
	return orElse(tables(values(args, "-c", "--command")), productionHosts)
}

func tables(texts []string) []string {
	var names []string
	for _, text := range texts {
		for _, found := range tableName.FindAllStringSubmatch(text, -1) {
			if name := strings.ToLower(strings.Trim(found[1], `"`)); !slices.Contains(names, name) {
				names = append(names, name)
			}
		}
	}
	return names
}

func namedOrWhole(named, args []string) []string {
	return orElse(named, []string{strings.Join(args, " ")})
}

func orElse(found, fallback []string) []string {
	if len(found) > 0 {
		return found
	}
	return fallback
}

func first(words []string) []string {
	return words[:min(1, len(words))]
}

func operands(args, valueFlags []string) []string {
	var words []string
	skip := false
	for _, arg := range args {
		switch {
		case arg == "--":
			return words
		case skip:
			skip = false
		case strings.HasPrefix(arg, "-"):
			skip = slices.Contains(valueFlags, arg)
		default:
			words = append(words, arg)
		}
	}
	return words
}

func values(args []string, flags ...string) []string {
	var found []string
	for index, arg := range args {
		flag, value, equals := strings.Cut(arg, "=")
		if !slices.Contains(flags, flag) {
			continue
		}
		if equals {
			found = append(found, value)
			continue
		}
		for _, following := range args[index+1:] {
			if strings.HasPrefix(following, "-") {
				break
			}
			found = append(found, following)
		}
	}
	return found
}

func reach(file *syntax.File, cwd string, nested bool, depth int) []reachedCall {
	var calls []reachedCall
	syntax.Walk(file, func(node syntax.Node) bool {
		stmt, ok := node.(*syntax.Stmt)
		if !ok {
			return true
		}
		for _, redir := range stmt.Redirs {
			if redir.Hdoc != nil {
				calls = append(calls, reachText(literal(redir.Hdoc), cwd, true, depth)...)
			}
		}
		expr, ok := stmt.Cmd.(*syntax.CallExpr)
		if !ok {
			return true
		}
		env, words := callWords(expr)
		if len(words) == 0 {
			return true
		}
		calls = append(calls, reachedCall{words: words, env: env, nested: nested})
		if name := path.Base(words[0]); slices.Contains(shells, name) && len(words) > 2 && strings.HasPrefix(words[1], "-") && strings.HasSuffix(words[1], "c") {
			calls = append(calls, reachText(words[2], cwd, nested, depth)...)
		}
		calls = append(calls, reachText(remoteCommand(words), cwd, true, depth)...)
		calls = append(calls, reachText(scriptText(words, cwd), cwd, true, depth)...)
		return true
	})
	return calls
}

func reachText(text, cwd string, nested bool, depth int) []reachedCall {
	if text == "" || depth == reachDepth {
		return nil
	}
	file, err := syntax.NewParser().Parse(strings.NewReader(text), "")
	if err != nil {
		return nil
	}
	return reach(file, cwd, nested, depth+1)
}

func callWords(call *syntax.CallExpr) (map[string]string, []string) {
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
	for len(words) > 0 && slices.Contains(wrappers, path.Base(words[0])) {
		words = words[1:]
		for len(words) > 0 && (strings.HasPrefix(words[0], "-") || strings.Contains(words[0], "=")) {
			if name, value, ok := strings.Cut(words[0], "="); ok && !strings.HasPrefix(name, "-") {
				env[name] = value
			}
			words = words[1:]
		}
	}
	return env, words
}

func remoteCommand(words []string) string {
	name, args := path.Base(words[0]), words[1:]
	if len(args) == 0 {
		return ""
	}
	if at := slices.Index(args, "--"); at >= 0 && (name == "sprite" && args[0] == "exec" || name == "nsc" && args[0] == "ssh") {
		return strings.Join(args[at+1:], " ")
	}
	switch {
	case name == "ssh":
		return strings.Join(rest(afterOptions(args, sshValueFlags)), " ")
	case name == "sprite" && args[0] == "exec":
		return strings.Join(afterOptions(args[1:], spriteValueFlags), " ")
	case name == "nsc" && args[0] == "ssh":
		return strings.Join(rest(afterOptions(args[1:], nil)), " ")
	}
	return ""
}

func rest(words []string) []string {
	return words[min(1, len(words)):]
}

func afterOptions(args, valueFlags []string) []string {
	index := 0
	for index < len(args) && strings.HasPrefix(args[index], "-") {
		if args[index] == "--" {
			return args[index+1:]
		}
		if slices.Contains(valueFlags, args[index]) {
			index++
		}
		index++
	}
	return args[min(index, len(args)):]
}

func scriptText(words []string, cwd string) string {
	named := words[0]
	if slices.Contains(shells, path.Base(named)) {
		named = ""
		if found := slices.IndexFunc(words[1:], func(word string) bool { return !strings.HasPrefix(word, "-") }); found >= 0 {
			named = words[found+1]
		}
	} else if !slices.Contains(scriptSuffixes, filepath.Ext(named)) {
		return ""
	}
	if named == "" || strings.ContainsAny(named, " \t\n") {
		return ""
	}
	if !filepath.IsAbs(named) {
		named = filepath.Join(cwd, named)
	}
	body, err := os.ReadFile(named)
	if err != nil {
		return ""
	}
	return string(body)
}

func approvalSeq(file *syntax.File) string {
	seq := ""
	syntax.Walk(file, func(node syntax.Node) bool {
		if comment, ok := node.(*syntax.Comment); ok {
			if found := approvalComment.FindStringSubmatch(comment.Text); found != nil {
				seq = found[1]
			}
		}
		return true
	})
	return seq
}

func (g Guard) approved(ctx context.Context, seq string, named []string, cwd string) bool {
	if seq == "" {
		return false
	}
	var record struct {
		Lane string `json:"lane"`
		Kind string `json:"kind"`
	}
	head, _, _ := strings.Cut(g.read(ctx, []string{"cci", "show", seq, "--json"}, cwd), "\n")
	if json.Unmarshal([]byte(head), &record) != nil || !slices.Contains(ownerLanes, record.Lane) || record.Kind != "owner" {
		return false
	}
	body := strings.ToLower(g.read(ctx, []string{"cci", "show", seq, "--body", "--budget", "0"}, cwd))
	for _, target := range named {
		if !strings.Contains(body, strings.ToLower(target)) {
			return false
		}
	}
	return true
}

func (p *production) names(key, value string) bool {
	if value == "" {
		return false
	}
	if p.config == nil {
		p.config = map[string][]string{}
		for _, line := range strings.Split(p.guard.read(p.ctx, productionConfigRead, p.cwd), "\n") {
			name, setting, _ := strings.Cut(line, " ")
			p.config[strings.ToLower(name)] = append(p.config[strings.ToLower(name)], strings.TrimSpace(setting))
		}
	}
	return slices.Contains(p.config[key], value)
}

func (p *production) currentContext() string {
	if p.kubeContext == nil {
		current := strings.TrimSpace(p.guard.read(p.ctx, currentContextRead, p.cwd))
		p.kubeContext = &current
	}
	return *p.kubeContext
}
