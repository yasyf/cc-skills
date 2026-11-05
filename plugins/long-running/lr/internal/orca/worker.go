package orca

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/carry"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	routeSprite       = "sprite"
	routeLocal        = "local"
	carriedBox        = "/home/sprite/drive/carried"
	sponsorKey        = "/home/sprite/.cc-inbox/node/key.pem"
	workerStartMS     = "600000"
	localAgentReads   = 120
	localIdleMS       = "120000"
	defaultLocalBase  = "origin/dev"
	allocationWarm    = "warm"
	allocationCreated = "created"
	idleWarning       = "so the prepared worker stays idle and unattached"
	sponsorEnv        = "WORKER_RELAY_SPONSOR"
	joinReceipt       = "relay-join.log"
	workerDriveDir    = "WORKER_DRIVE_DIR"
	workerLandingRepo = "WORKER_LANDING_REPO"
	seedReceipt       = "store-seed.log"
	seedCopy          = "store-seed.gz"
	lrPlatform        = "linux-x86_64"
	toolsMissing      = 3
	beforeWorker      = "before-worker.sh"
	notesRefs         = "+refs/cc-notes/*:refs/cc-notes/*"
	notesReceipt      = "notes.log"
	workerAwake       = "worker-awake"
	holdReceipt       = "awake.log"
	holdTimeout       = time.Minute
	holdWorker        = `sprite-env services create ` + workerAwake + ` --cmd "$HOME/.local/bin/lr" --args sprites,hold --no-stream`
	fetchNotesScript  = `IFS= read -r NOTES_TOKEN; export NOTES_TOKEN; exec git -c credential.helper= -c 'credential.helper=!f() { echo username=x-access-token; echo "password=$NOTES_TOKEN"; }; f' -C "$1" fetch --quiet origin "$2"`
	unpackCarried     = `mkdir -p "$1" && tar -xzf - -C "$1"`
	seedStore         = `cci relay export --drive "$1" > "$3" && sprite exec -s "$2" --no-stdin --file "$3:/tmp/store-seed.gz" -- sh -c 'sprite-env services create store-seed --cmd sh --args "-c,$HOME/.local/bin/cci relay seed --drive $1 < /tmp/store-seed.gz; rm /tmp/store-seed.gz; sprite-env services delete store-seed" --no-stream' sh "$1"; rm -f "$3"`
	joinRelay         = `CCI_SERVICE_KEY=$(cat); export CCI_SERVICE_KEY; ~/.local/bin/cci relay join --instance --drive "$1" --url "$2" --ttl 24h --owner "$3" && sprite-env services create cci --cmd "$HOME/.local/bin/cci" --args supervise --no-stream`
)

var (
	joinWaits    = []time.Duration{5 * time.Second, 15 * time.Second, 45 * time.Second}
	spriteLane   = regexp.MustCompile(`\A[a-z0-9][a-z0-9-]{0,54}\z`)
	fullCommit   = regexp.MustCompile(`\A[0-9a-f]{40}([0-9a-f]{24})?\z`)
	orcaIdent    = regexp.MustCompile(`\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\z`)
	briefTool    = regexp.MustCompile("(?m)(?:^|[\\s`(])(ccn|cc-notes|cci|lr|gh|ccx) [a-z]")
	modelAliases = map[string]string{"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "fable": "claude-fable-5-1"}
)

type WorkerStarter struct {
	Runner shell.Runner
	Env    func(string) string
	Sleep  func(time.Duration)
	Stderr io.Writer
	Spawn  func(argv []string, log string, env []string) (Process, error)
}

type workerLaunch struct {
	*WorkerStarter
	ctx context.Context

	lane, effort, brief, run, ref, expect, tier   string
	modelID, kind, route, dir, stem, attempt, sha string
	checkout, ccRemote, config                    string
	pins                                          spritePins
	terminal, worktree                            string
	bytes                                         int
}

type workerRequest struct {
	Lane       string  `json:"lane"`
	Run        string  `json:"run"`
	Route      string  `json:"route"`
	Agent      agent   `json:"agent"`
	Ref        *string `json:"ref"`
	ExpectBase *string `json:"expectBase"`
	Brief      struct {
		Path   string `json:"path"`
		SHA256 string `json:"sha256"`
		Bytes  int    `json:"bytes"`
	} `json:"brief"`
}

type agent struct {
	Kind        string  `json:"kind"`
	Model       string  `json:"model"`
	Effort      string  `json:"effort"`
	ServiceTier *string `json:"serviceTier"`
}

type workerStarted struct {
	State    string `json:"state"`
	Task     string `json:"task"`
	Dispatch string `json:"dispatch"`
	Terminal string `json:"terminal"`
	Worktree string `json:"worktree"`
}

type spriteStarted struct {
	workerStarted
	Lane       string `json:"lane"`
	Workspace  string `json:"workspace"`
	Allocation string `json:"allocation"`
}

func given(value string) *string {
	if value == "" {
		return nil
	}
	return &value
}

func (s *WorkerStarter) Run(ctx context.Context, lane, model, effort, brief string) (string, error) {
	w := &workerLaunch{WorkerStarter: s, ctx: ctx, lane: lane, effort: effort, brief: brief}
	return w.start(model)
}

func refuse(format string, args ...any) error {
	return &Failed{fmt.Sprintf(format, args...)}
}

func attemptOf(brief string) string {
	return strings.TrimSuffix(brief, filepath.Ext(brief)) + ".worker"
}

func (w *workerLaunch) capture(argv ...string) (string, string, int) {
	result, err := w.Runner.Run(w.ctx, shell.Command{Argv: argv})
	var exit *shell.ExitError
	switch {
	case err == nil:
		return result.Stdout, result.Stderr, 0
	case errors.As(err, &exit):
		return result.Stdout, result.Stderr, exit.Code
	}
	return result.Stdout, result.Stderr + err.Error(), max(result.Code, 1)
}

func (w *workerLaunch) receipt(name, text string) error {
	return os.WriteFile(filepath.Join(w.attempt, name), []byte(text), 0o600)
}

func (w *workerLaunch) start(model string) (string, error) {
	if w.run = w.Env("WORKER_RUN"); w.run == "" {
		return "", refuse("set WORKER_RUN to the Orca Run id")
	}
	w.ref, w.expect, w.tier = w.Env("WORKER_REF"), w.Env("WORKER_EXPECT_BASE"), w.Env("WORKER_SERVICE_TIER")
	if info, err := os.Stat(w.brief); err != nil || !info.Mode().IsRegular() {
		return "", refuse("no brief at %s", w.brief)
	}
	brief, err := filepath.Abs(w.brief)
	if err != nil {
		return "", err
	}
	name := filepath.Base(brief)
	w.brief, w.dir, w.stem = brief, filepath.Dir(brief), strings.TrimSuffix(name, filepath.Ext(name))
	w.attempt = attemptOf(brief)
	if err := w.resolveRoute(model); err != nil {
		return "", err
	}
	if w.route == routeSprite {
		if err := w.checkSprite(); err != nil {
			return "", err
		}
	} else if err := w.checkLocal(); err != nil {
		return "", err
	}
	if bound := w.boundRun(); bound != w.run {
		handle, owner := w.Env("ORCA_TERMINAL_HANDLE"), bound
		if handle == "" {
			handle = "unset"
		}
		if owner == "" {
			owner = "no Run"
		}
		return "", refuse("terminal %s coordinates %s, not %s, so nothing launched; launch from the coordinator that already owns Run %s", handle, owner, w.run, w.run)
	}
	if err := w.claimAttempt(); err != nil {
		return "", err
	}
	if w.route == routeSprite {
		if err := w.carryBrief(); err != nil {
			return "", err
		}
	}
	if err := w.writeRequest(); err != nil {
		return "", err
	}
	if w.route == routeSprite {
		return w.startSprite()
	}
	return w.startLocal()
}

func (w *workerLaunch) resolveRoute(model string) error {
	w.modelID = model
	if id, aliased := modelAliases[model]; aliased {
		w.modelID = id
	}
	switch {
	case strings.HasPrefix(w.modelID, "claude-"):
		w.kind = agentClaude
	case strings.HasPrefix(w.modelID, "gpt-"):
		w.kind = agentCodex
	default:
		return refuse("model %s is neither a claude- nor a gpt- model, so neither claude nor codex runs it; nothing launched", w.modelID)
	}
	if w.tier != "" && w.kind != agentCodex {
		return refuse("WORKER_SERVICE_TIER is a codex setting, and claude runs %s; nothing launched", w.modelID)
	}
	if w.tier == "fast" && w.Env("WORKER_INCIDENT") != "1" {
		return refuse("WORKER_SERVICE_TIER=fast is for incident lanes only; set WORKER_INCIDENT=1 for an incident lane; nothing launched")
	}
	switch w.Env("WORKER_LOCAL") {
	case "":
		w.route = routeSprite
	case "1":
		w.route = routeLocal
	default:
		return refuse("WORKER_LOCAL is 1 for the explicit local route or unset for a Sprite; nothing launched")
	}
	return nil
}

func (w *workerLaunch) checkLocal() error {
	if w.kind != agentClaude {
		return refuse("the local route starts only claude, and codex runs %s; nothing launched", w.modelID)
	}
	if w.ref+w.expect != "" {
		return refuse("WORKER_REF and WORKER_EXPECT_BASE pick a Sprite's checkout; the local route takes WORKER_BASE_BRANCH; nothing launched")
	}
	if w.Env("WORKER_REPO") == "" {
		return refuse("set WORKER_REPO to the Orca repository the local route adds its worktree to; nothing launched")
	}
	return nil
}

func (w *workerLaunch) checkSprite() error {
	if !spriteLane.MatchString(w.lane) {
		return refuse("a Sprite lane takes up to 55 lowercase letters, digits, and dashes, starting with a letter or digit; nothing was prepared")
	}
	if fableModel.MatchString(w.modelID) {
		return refuse("%s is Fable, which runs only on the explicit local route, WORKER_LOCAL=1; nothing was prepared, and nothing launched locally instead", w.modelID)
	}
	if w.Env("WORKER_REPO") != "" {
		return refuse("WORKER_REPO picks a local Orca repository; a Sprite checks out the repository its cc-remote config names, chosen with CC_REMOTE_CONFIG; nothing was prepared")
	}
	if w.Env("WORKER_BASE_BRANCH") != "" {
		return refuse("WORKER_BASE_BRANCH picks a local worktree's base; a Sprite checks out WORKER_REF, a branch or tag, or the cc-remote config's ref; nothing was prepared")
	}
	if w.expect != "" && !fullCommit.MatchString(w.expect) {
		return refuse("WORKER_EXPECT_BASE is a full 40- or 64-character lowercase commit; nothing was prepared")
	}
	if w.Env(sponsorEnv) == "" {
		return refuse("set %s to the Sprite holding the drive's enrolled cci service key, because a worker with no relay posts nothing anyone reads; nothing was prepared", sponsorEnv)
	}
	checkout, err := filepath.EvalSymlinks(w.Env("WORKER_CHECKOUT"))
	if w.Env("WORKER_CHECKOUT") == "" || err != nil {
		return refuse("set WORKER_CHECKOUT to the checkout whose tools/cc-remote starts Sprites")
	}
	if w.checkout, err = filepath.Abs(checkout); err != nil {
		return err
	}
	if w.pins, err = readPins(w.Env); err != nil {
		return err
	}
	tools := filepath.Join(w.checkout, "tools", "cc-remote")
	if w.ccRemote = w.Env("WORKER_CC_REMOTE"); w.ccRemote == "" {
		w.ccRemote = filepath.Join(tools, "bin", "cc-remote")
	}
	if _, err := exec.LookPath(w.ccRemote); err != nil {
		return refuse("WORKER_CC_REMOTE names no cc-remote command at %s; unset it to run this checkout's pinned one; nothing was prepared", w.ccRemote)
	}
	if w.config = w.Env("CC_REMOTE_CONFIG"); w.config == "" {
		w.config = filepath.Join(tools, "config.yaml")
	}
	if !filepath.IsAbs(w.config) {
		if w.config, err = filepath.Abs(w.config); err != nil {
			return err
		}
	}
	own, ownErr := os.Stat(filepath.Join(tools, "config.yaml"))
	chosen, chosenErr := os.Stat(w.config)
	if ownErr != nil || chosenErr != nil || !os.SameFile(own, chosen) {
		return nil
	}
	return w.checkPin(tools)
}

func (w *workerLaunch) checkPin(tools string) error {
	payload := filepath.Join(tools, "agents-payload.json")
	var pinned struct {
		BootstrapHelper struct {
			Version string `json:"version"`
		} `json:"bootstrapHelper"`
	}
	raw, err := os.ReadFile(payload)
	if err != nil || json.Unmarshal(raw, &pinned) != nil || pinned.BootstrapHelper.Version == "" {
		return refuse("%s pins no bootstrap helper version; nothing was prepared", payload)
	}
	pin := pinned.BootstrapHelper.Version
	result, err := w.Runner.Run(w.ctx, shell.Command{Argv: []string{w.ccRemote, "version"}, Env: []string{"CC_REMOTE_CONFIG=" + w.config}})
	printed := strings.TrimRight(result.Stderr+result.Stdout, "\n")
	if err != nil {
		return refuse("%s version failed: %s; nothing was prepared", w.ccRemote, printed)
	}
	version := printed[strings.LastIndex(printed, "\n")+1:]
	if version == pin {
		return nil
	}
	if version == "" {
		version = "nothing"
	}
	return refuse("%s version printed %s, and %s pins bootstrap helper %s; a Sprite needs the CLI at exactly its helper's release; nothing was prepared", w.ccRemote, version, w.config, pin)
}

func oneDocument(raw string) (json.RawMessage, bool) {
	decoder := json.NewDecoder(strings.NewReader(raw))
	var doc, rest json.RawMessage
	if decoder.Decode(&doc) != nil {
		return nil, false
	}
	return doc, errors.Is(decoder.Decode(&rest), io.EOF)
}

func (w *workerLaunch) boundRun() string {
	stdout, _, code := w.capture("orca", orchestration, "run-current", "--full", "--json")
	doc, single := oneDocument(stdout)
	if code != 0 || !single {
		return ""
	}
	var current struct {
		OK     bool            `json:"ok"`
		Error  json.RawMessage `json:"error"`
		Result struct {
			Run struct {
				ID string `json:"id"`
			} `json:"run"`
		} `json:"result"`
	}
	if json.Unmarshal(doc, &current) != nil || !current.OK || (current.Error != nil && string(current.Error) != "null") {
		return ""
	}
	return current.Result.Run.ID
}

func present(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

func (w *workerLaunch) claimAttempt() error {
	legacy := filepath.Join(w.dir, w.stem+".dispatch.json")
	if present(legacy) {
		return refuse("%s holds an earlier launch of this brief, and a brief gets one attempt; inspect it instead", legacy)
	}
	if exists(filepath.Join(w.attempt, "worker-start.json")) {
		return refuse("%s holds an earlier attempt of this brief, and a brief gets one attempt; inspect it instead", w.attempt)
	}
	if present(w.attempt) {
		if err := os.Rename(w.attempt, w.attempt+"."+strconv.FormatInt(time.Now().UnixNano(), 10)); err != nil {
			return err
		}
	}
	return os.Mkdir(w.attempt, 0o750)
}

func (w *workerLaunch) carryBrief() error {
	raw, err := os.ReadFile(w.brief)
	if err != nil {
		return err
	}
	notes := w.Env("WORKER_NOTES_REPO")
	if notes == "" {
		notes = w.checkout
	}
	carrier := carry.Carrier{Runner: w.Runner, Home: w.Env("HOME"), Repo: notes, Into: filepath.Join(w.attempt, "carried"), Box: carriedBox, Vars: map[string]string{carry.DriveDir: w.Env(workerDriveDir)}}
	rewritten, err := carrier.Carry(w.ctx, string(raw))
	if err != nil {
		return refuse("brief %s names what no Sprite can receive: %v; nothing was prepared", w.brief, err)
	}
	w.brief = filepath.Join(w.attempt, briefFile)
	return os.WriteFile(w.brief, []byte(rewritten), 0o600)
}

func (w *workerLaunch) writeRequest() error {
	raw, err := os.ReadFile(w.brief)
	if err != nil {
		return err
	}
	sum := sha256.Sum256(raw)
	w.sha, w.bytes = hex.EncodeToString(sum[:]), len(raw)
	request := workerRequest{
		Lane: w.lane, Run: w.run, Route: w.route,
		Agent: agent{Kind: w.kind, Model: w.modelID, Effort: w.effort, ServiceTier: given(w.tier)},
		Ref:   given(w.ref), ExpectBase: given(w.expect),
	}
	request.Brief.Path, request.Brief.SHA256, request.Brief.Bytes = w.brief, w.sha, w.bytes
	written, err := json.MarshalIndent(request, "", "  ")
	if err != nil {
		return err
	}
	return w.receipt("request.json", string(written)+"\n")
}

func (w *workerLaunch) startWorker(spec string, placement ...string) (string, string, error) {
	argv := append(append([]string{"orca", orchestration, "worker-start", "--run", w.run, "--spec", spec, "--task-title", w.lane}, placement...), "--timeout-ms", workerStartMS, "--full", "--json")
	stdout, stderr, code := w.capture(argv...)
	tries := 1
	for ; code != 0 && tries <= len(joinWaits); tries++ {
		request := replayable(stdout)
		if request == "" {
			break
		}
		w.Sleep(joinWaits[tries-1])
		stdout, stderr, code = w.capture(append(slices.Clone(argv), "--retry-request", request)...)
	}
	for name, text := range map[string]string{"worker-start.json": stdout, "worker-start.err": stderr, "worker-start.status": strconv.Itoa(code) + "\n"} {
		if err := w.receipt(name, text); err != nil {
			return "", "", err
		}
	}
	if code != 0 {
		var refusal struct {
			OK    *bool `json:"ok"`
			Error struct {
				Code    string `json:"code"`
				Message string `json:"message"`
			} `json:"error"`
		}
		said := ""
		if json.Unmarshal([]byte(stdout), &refusal) == nil && refusal.OK != nil && !*refusal.OK {
			said = ": " + refusal.Error.Code + ": " + refusal.Error.Message
		}
		replayed := ""
		if tries > 1 {
			replayed = fmt.Sprintf(" after %d tries", tries)
		}
		return "", "", refuse("orca orchestration worker-start exited %d%s%s; kept terminal %s, worktree %s, and the receipt in %s", code, replayed, said, w.terminal, w.worktree, w.attempt)
	}
	unready := refuse("orca orchestration worker-start exited 0 without a ready receipt for Run %s; kept terminal %s, worktree %s, and the receipt in %s", w.run, w.terminal, w.worktree, w.attempt)
	doc, single := oneDocument(stdout)
	if !single {
		return "", "", unready
	}
	var receipt struct {
		OK     bool            `json:"ok"`
		Error  json.RawMessage `json:"error"`
		Result struct {
			State      string `json:"state"`
			RunID      string `json:"runId"`
			TaskID     string `json:"taskId"`
			DispatchID string `json:"dispatchId"`
		} `json:"result"`
	}
	if json.Unmarshal(doc, &receipt) != nil || !receipt.OK || (receipt.Error != nil && string(receipt.Error) != "null") || receipt.Result.State != "ready" || receipt.Result.RunID != w.run || !orcaIdent.MatchString(receipt.Result.TaskID) || !orcaIdent.MatchString(receipt.Result.DispatchID) {
		return "", "", unready
	}
	return receipt.Result.TaskID, receipt.Result.DispatchID, nil
}

func allDocuments(raw string) ([]any, bool) {
	decoder := json.NewDecoder(strings.NewReader(raw))
	var docs []any
	for {
		var doc any
		err := decoder.Decode(&doc)
		if errors.Is(err, io.EOF) {
			return docs, true
		}
		if err != nil {
			return nil, false
		}
		docs = append(docs, doc)
	}
}

func textOf(value any) (string, bool) {
	word, isText := value.(string)
	return word, isText
}

func table(value any) map[string]any {
	found, _ := value.(map[string]any)
	return found
}

func filled(values ...any) bool {
	for _, value := range values {
		if word, isText := textOf(value); !isText || word == "" {
			return false
		}
	}
	return true
}

func (w *workerLaunch) allocationFault(raw string) (string, string) {
	docs, readable := allDocuments(raw)
	if !readable {
		return "not readable as JSON", ""
	}
	if len(docs) != 1 || table(docs[0]) == nil {
		return "not one JSON object", ""
	}
	made := table(docs[0])
	workspace, named := textOf(made["workspace"])
	kind := made["allocation"]
	ref, hasRef := textOf(made["ref"])
	task := table(made["task"])
	brief := table(task["brief"])
	taskAgent := table(task["agent"])
	tier, _ := textOf(taskAgent["serviceTier"])
	base, _ := textOf(task["baseCommit"])
	root, _ := textOf(task["projectRoot"])
	repoID, _ := textOf(task["repoId"])
	switch {
	case made["schemaVersion"] != float64(1) || made["lane"] != w.lane:
		return "an allocation other than schema 1 for lane " + w.lane, ""
	case !named || !spriteLane.MatchString(workspace):
		return "an invalid allocated workspace", ""
	case (kind != allocationWarm && kind != allocationCreated) || (kind == allocationCreated && workspace != w.lane) || (kind == allocationWarm && workspace == w.lane):
		return "an allocation kind inconsistent with its workspace", ""
	case !hasRef || ref == "" || (w.ref != "" && ref != w.ref):
		return "a ref other than the requested branch or tag", ""
	case task == nil || task["workspace"] != workspace || task["provider"] != "sprites" || task["profile"] != "agents":
		return "a task other than the allocated Sprite agents workspace", ""
	case task["prepared"] != true:
		return "prepared is not true", ""
	case !filled(task["managedServer"]):
		return "an Orca runtime serving on Electron, not its managed server", ""
	case !reflect.DeepEqual(made["head"], task["baseCommit"]):
		return "an allocation HEAD inconsistent with its task", ""
	case taskAgent["kind"] != w.kind || taskAgent["model"] != w.modelID || taskAgent["effort"] != w.effort || tier != w.tier:
		return fmt.Sprintf("an agent other than %s %s at %s effort", w.kind, w.modelID, w.effort), ""
	case brief["sha256"] != w.sha || brief["bytes"] != float64(w.bytes):
		return fmt.Sprintf("a brief other than %d bytes with SHA-256 %s", w.bytes, w.sha), ""
	case !filled(task["environment"], task["terminal"], task["projectRoot"], brief["path"]) || task["worktreeId"] != repoID+"::"+root:
		return "a task without its environment, terminal, repoId::projectRoot worktree, and brief path", ""
	}
	return "", base
}

func (w *workerLaunch) prepareSprite() (map[string]any, error) {
	raw, err := os.ReadFile(w.brief)
	if err != nil {
		return nil, err
	}
	var needs []string
	for _, match := range briefTool.FindAllSubmatch(raw, -1) {
		needs = append(needs, string(match[1]))
	}
	slices.Sort(needs)
	if err := w.receipt(beforeWorker, w.pins.installScript(slices.Compact(needs))); err != nil {
		return nil, err
	}
	argv := []string{w.ccRemote, "orca", "prepare", w.lane, "--warm", "--provider", "sprites", "--profile", "agents", "--agent", w.kind, "--model", w.modelID, "--effort", w.effort, "--brief-file", w.brief, "--before-worker-file", filepath.Join(w.attempt, beforeWorker), "--env", laneEnv + "=" + w.lane}
	if drive := driveName(w.Env, w.run); drive != "" {
		argv = append(argv, "--env", driveEnv+"="+drive)
	}
	if repo := w.Env(workerLandingRepo); repo != "" {
		argv = append(argv, "--env", landingRepoEnv+"="+repo)
	}
	if w.ref != "" {
		argv = append(argv, "--ref", w.ref)
	}
	if w.tier != "" {
		argv = append(argv, "--service-tier", w.tier)
	}
	if mcp := w.Env("WORKER_MCP_CONFIG"); mcp != "" {
		argv = append(argv, "--mcp-config", mcp)
	}
	result, err := w.Runner.Run(w.ctx, shell.Command{Argv: argv, Env: []string{"CC_REMOTE_CONFIG=" + w.config}})
	code := 0
	if err != nil {
		code = max(result.Code, 1)
	}
	for name, written := range map[string]string{"allocation.json": result.Stdout, "prepare.err": result.Stderr, "prepare.status": strconv.Itoa(code) + "\n"} {
		if err := w.receipt(name, written); err != nil {
			return nil, err
		}
	}
	if _, missing, found := strings.Cut(result.Stderr, "missing:"); code != 0 && found {
		missing, _, _ = strings.Cut(missing, "\n")
		return nil, refuse("the Sprite has no %s, which the brief runs, so no worker started; %s keeps its output", strings.Join(strings.Fields(missing), ", "), filepath.Join(w.attempt, "prepare.err"))
	}
	if code != 0 {
		return nil, refuse("cc-remote orca prepare exited %d; %s keeps its output, and nothing was attached or retried", code, w.attempt)
	}
	fault, base := w.allocationFault(result.Stdout)
	if fault != "" {
		return nil, refuse("%s holds %s, %s", filepath.Join(w.attempt, "allocation.json"), fault, idleWarning)
	}
	var made struct {
		Task json.RawMessage `json:"task"`
	}
	if err := json.Unmarshal([]byte(result.Stdout), &made); err != nil {
		return nil, err
	}
	if err := w.receipt("prepared.json", string(made.Task)+"\n"); err != nil {
		return nil, err
	}
	if w.expect != "" && base != w.expect {
		return nil, refuse("the Sprite checked out %s, not WORKER_EXPECT_BASE %s, %s, and nothing is prepared again", base, w.expect, idleWarning)
	}
	var allocation map[string]any
	if err := json.Unmarshal([]byte(result.Stdout), &allocation); err != nil {
		return nil, err
	}
	return allocation, nil
}

func (w *workerLaunch) copyCarried(workspace string) error {
	carried := filepath.Join(w.attempt, "carried")
	if !isDir(carried) {
		return nil
	}
	log := filepath.Join(w.attempt, "carried.log")
	failure := refuse("copying the files the brief names to Sprite %s failed; %s keeps its output, %s", workspace, log, idleWarning)
	packed, err := w.Runner.Run(w.ctx, shell.Command{Argv: []string{"tar", "-C", carried, "-czf", "-", "."}, Env: []string{"COPYFILE_DISABLE=1"}})
	said := packed.Stderr
	if err == nil {
		var sent shell.Result
		sent, err = w.Runner.Run(w.ctx, shell.Command{Argv: []string{"sprite", "exec", "-s", workspace, "--", "sh", "-c", unpackCarried, "sh", carriedBox}, Stdin: packed.Stdout})
		said += sent.Stdout + sent.Stderr
	}
	if written := w.receipt("carried.log", said); written != nil {
		return written
	}
	if err != nil {
		return failure
	}
	return nil
}

func (w *workerLaunch) fetchNotes(workspace string) error {
	token, said, code := w.capture("gh", "auth", "token")
	if code != 0 {
		return w.receipt(notesReceipt, said)
	}
	fetched, err := w.Runner.Run(w.ctx, shell.Command{Argv: []string{"sprite", "exec", "-s", workspace, "--", "sh", "-c", fetchNotesScript, "sh", w.worktree, notesRefs}, Stdin: token})
	said = fetched.Stdout + fetched.Stderr
	if err != nil {
		said += err.Error() + "\n"
	}
	return w.receipt(notesReceipt, said)
}

func (w *workerLaunch) seedDesk(workspace string) error {
	drive := driveName(w.Env, w.run)
	if drive == "" {
		return nil
	}
	log := filepath.Join(w.attempt, seedReceipt)
	if _, err := w.Spawn([]string{"sh", "-c", seedStore, "sh", drive, workspace, filepath.Join(w.attempt, seedCopy)}, log, nil); err != nil {
		return w.receipt(seedReceipt, err.Error()+"\n")
	}
	return nil
}

func (w *workerLaunch) join(workspace, sponsor string) (string, error) {
	var log bytes.Buffer
	status, errs, code := w.capture("cci", "relay", "status", "--json")
	log.WriteString(errs)
	if code != 0 {
		return log.String(), errors.New("cci relay status failed")
	}
	var meshes []struct {
		Mesh   string   `json:"mesh"`
		URL    string   `json:"url"`
		Routes []string `json:"routes"`
	}
	if err := json.Unmarshal([]byte(status), &meshes); err != nil {
		return log.String(), err
	}
	mesh := -1
	for i, candidate := range meshes {
		if candidate.Mesh == "default" && len(candidate.Routes) > 0 {
			mesh = i
			break
		}
	}
	if mesh < 0 {
		return log.String(), errors.New("no default mesh with routes")
	}
	node, errs, code := w.capture("cci", "node", "--json")
	log.WriteString(errs)
	var owner struct {
		PublicKey string `json:"public_key"`
	}
	if code != 0 || json.Unmarshal([]byte(node), &owner) != nil || owner.PublicKey == "" {
		return log.String(), errors.New("cci node names no public key")
	}
	key, errs, code := w.capture("sprite", "exec", "-s", sponsor, "--no-stdin", "--", "cat", sponsorKey)
	log.WriteString(errs)
	if code != 0 {
		return log.String(), errors.New("the sponsor's service key did not read")
	}
	for tries := 1; ; tries++ {
		joined, err := w.Runner.Run(w.ctx, shell.Command{
			Argv:  []string{"sprite", "exec", "-s", workspace, "--", "sh", "-c", joinRelay, "sh", strings.Join(meshes[mesh].Routes, ","), meshes[mesh].URL, owner.PublicKey},
			Stdin: key,
		})
		log.WriteString(joined.Stdout + joined.Stderr)
		if err == nil {
			return log.String(), nil
		}
		if tries > len(joinWaits) {
			said := strings.TrimSpace(joined.Stdout + joined.Stderr)
			return log.String(), fmt.Errorf("%d tries, the last saying %s", tries, said[strings.LastIndex(said, "\n")+1:])
		}
		w.Sleep(joinWaits[tries-1])
	}
}

func (w *workerLaunch) startSprite() (string, error) {
	allocation, err := w.prepareSprite()
	if err != nil {
		return "", err
	}
	workspace, _ := textOf(allocation["workspace"])
	task := table(allocation["task"])
	if err := w.copyCarried(workspace); err != nil {
		return "", err
	}
	raw, err := os.ReadFile(w.brief)
	if err != nil {
		return "", err
	}
	installed, _ := textOf(task["beforeWorker"])
	if err := w.receipt("install.log", installed); err != nil {
		return "", err
	}
	w.worktree, _ = textOf(task["projectRoot"])
	if err := w.fetchNotes(workspace); err != nil {
		return "", err
	}
	sponsor := w.Env(sponsorEnv)
	log, joinErr := w.join(workspace, sponsor)
	if err := w.receipt(joinReceipt, log); err != nil {
		return "", err
	}
	if joinErr != nil {
		return "", refuse("Sprite %s did not join the cci relay with %s's service key: %v; nothing started, because a worker with no relay posts nothing anyone reads; %s keeps the output, and the prepared worker stays idle and unattached", workspace, sponsor, joinErr, filepath.Join(w.attempt, joinReceipt))
	}
	w.terminal, _ = textOf(task["terminal"])
	desk := ""
	if briefDesk.Match(raw) {
		desk = standingDesk
	}
	noPR := ""
	if briefNoPR.Match(raw) {
		noPR = noPRLine
	}
	read := ""
	if briefRead.Match(raw) && placedLocal(string(raw)) {
		read = readOnSprite
	}
	if stampedMacInputs(string(raw)) {
		read = macInputsOnSprite
	}
	path, _ := textOf(table(task["brief"])["path"])
	environment, _ := textOf(task["environment"])
	worktreeID, _ := textOf(task["worktreeId"])
	spec := fmt.Sprintf("Lane %s: read the whole brief at %s, verify its SHA-256 is %s and its size is %d bytes, then follow it exactly.%s%s%s%s", w.lane, path, w.sha, w.bytes, spriteBuildkite, desk, noPR, read)
	taskID, dispatch, err := w.startWorker(spec, "--on", environment, "--worktree", "id:"+worktreeID, "--terminal", w.terminal)
	if err != nil {
		return "", err
	}
	if err := w.holdAwake(workspace); err != nil {
		return "", err
	}
	if desk != "" {
		if err := w.seedDesk(workspace); err != nil {
			return "", err
		}
	}
	made, _ := textOf(allocation["allocation"])
	line, err := json.Marshal(spriteStarted{workerStarted{"ready", taskID, dispatch, w.terminal, w.worktree}, w.lane, workspace, made})
	return string(line), err
}

func (w *workerLaunch) holdAwake(workspace string) error {
	held, err := w.Runner.Run(w.ctx, shell.Command{Argv: []string{"sprite", "exec", "-s", workspace, "--no-stdin", "--", "sh", "-c", holdWorker}, Timeout: holdTimeout})
	if err == nil {
		return nil
	}
	fmt.Fprintf(w.Stderr, "warning: Sprite %s holds no %s task, so lane %s dies if the Sprite goes cold; %s keeps the output\n", workspace, workerAwake, w.lane, filepath.Join(w.attempt, holdReceipt))
	return w.receipt(holdReceipt, held.Stdout+held.Stderr+err.Error()+"\n")
}

func (w *workerLaunch) orcaJSON(args ...string) (string, bool) {
	stdout, stderr, code := w.capture(append(append([]string{"orca"}, args...), "--json")...)
	if code != 0 {
		fmt.Fprintf(w.Stderr, "orca %s %s failed: %s\n", args[0], args[1], strings.TrimRight(stdout+stderr, "\n"))
		return "", false
	}
	return stdout, true
}

func (w *workerLaunch) namedWorktree(repo string) (string, error) {
	said, ok := w.orcaJSON("worktree", "list", "--repo", "id:"+repo, "--limit", "10000")
	if !ok {
		return "", refuse("no worktree named %s", w.lane)
	}
	var listed struct {
		Result struct {
			Worktrees []struct {
				DisplayName string `json:"displayName"`
				Path        string `json:"path"`
			} `json:"worktrees"`
		} `json:"result"`
	}
	if err := json.Unmarshal([]byte(said), &listed); err != nil {
		return "", err
	}
	for _, worktree := range listed.Result.Worktrees {
		if worktree.DisplayName == w.lane {
			return worktree.Path, nil
		}
	}
	return "", nil
}

func (w *workerLaunch) openLocalTerminal(existing string) error {
	if existing == "" {
		said, ok := w.orcaJSON("terminal", "list", "--worktree", "path:"+w.worktree)
		if !ok {
			return refuse("no terminal in %s", w.worktree)
		}
		var listed listedTerminals
		if err := json.Unmarshal([]byte(said), &listed); err != nil {
			return err
		}
		for _, terminal := range listed.Result.Terminals {
			if terminal.Agent == "" {
				w.terminal = terminal.Handle
				return nil
			}
		}
	}
	said, ok := w.orcaJSON("terminal", "create", "--worktree", "path:"+w.worktree, "--title", w.lane)
	if !ok {
		return refuse("no terminal in %s", w.worktree)
	}
	var created struct {
		Result struct {
			Terminal struct {
				Handle string `json:"handle"`
			} `json:"terminal"`
		} `json:"result"`
	}
	if err := json.Unmarshal([]byte(said), &created); err != nil {
		return err
	}
	if created.Result.Terminal.Handle == "" {
		return refuse("no terminal in %s", w.worktree)
	}
	w.terminal = created.Result.Terminal.Handle
	return nil
}

func (w *workerLaunch) startLocal() (string, error) {
	repo := w.Env("WORKER_REPO")
	base := w.Env("WORKER_BASE_BRANCH")
	if base == "" {
		base = defaultLocalBase
	}
	existing, err := w.namedWorktree(repo)
	if err != nil {
		return "", err
	}
	w.worktree = existing
	if existing == "" {
		if said, ok := w.orcaJSON("worktree", "create", "--name", w.lane, "--repo", "id:"+repo, "--base-branch", base, "--setup", "run"); ok {
			var created struct {
				Result struct {
					Worktree struct {
						Path string `json:"path"`
					} `json:"worktree"`
				} `json:"result"`
			}
			if json.Unmarshal([]byte(said), &created) == nil {
				w.worktree = created.Result.Worktree.Path
			}
		}
		if w.worktree == "" {
			if w.worktree, err = w.namedWorktree(repo); err != nil {
				return "", err
			}
		}
	}
	if w.worktree == "" {
		return "", refuse("no worktree named %s", w.lane)
	}
	if err := w.openLocalTerminal(existing); err != nil {
		return "", err
	}
	if err := w.bootClaude(); err != nil {
		return "", err
	}
	spec := "Lane '" + w.lane + "'. Your full brief is the file " + w.brief + ": read it in full with the Read tool before anything else and execute it exactly (Orca truncates pasted specs). Your Orca worktree is " + w.worktree + ". You run in bypass-permissions mode; the brief's Escalate rules still hold."
	taskID, dispatch, err := w.startWorker(spec, "--worktree", "path:"+w.worktree, "--terminal", w.terminal)
	if err != nil {
		return "", err
	}
	line, err := json.Marshal(workerStarted{"ready", taskID, dispatch, w.terminal, w.worktree})
	return string(line), err
}

func (w *workerLaunch) bootClaude() error {
	command := "env " + claudeLaneEnv + " claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions --disallowedTools " + laneDeniedTools + " --strict-mcp-config --no-chrome --model " + shellQuote(w.modelID) + " --effort " + shellQuote(w.effort)
	if mcp := w.Env("WORKER_MCP_CONFIG"); mcp != "" {
		command += " --mcp-config " + shellQuote(mcp)
	}
	command += laneSettingsFlag(w.Env)
	if _, ok := w.orcaJSON("terminal", "send", "--terminal", w.terminal, "--enter", "--text", command); !ok {
		return refuse("claude never started in %s; kept it and worktree %s", w.terminal, w.worktree)
	}
	started := false
	for range localAgentReads {
		said, ok := w.orcaJSON("terminal", "show", "--terminal", w.terminal)
		if !ok {
			break
		}
		var shown struct {
			Result struct {
				Terminal struct {
					Agent string `json:"agentIdentity"`
				} `json:"terminal"`
			} `json:"result"`
		}
		if json.Unmarshal([]byte(said), &shown) == nil && shown.Result.Terminal.Agent == agentClaude {
			started = true
			break
		}
		w.Sleep(time.Second)
	}
	if !started {
		return refuse("claude never started in %s; kept it and worktree %s", w.terminal, w.worktree)
	}
	if _, ok := w.orcaJSON("terminal", "wait", "--terminal", w.terminal, "--for", "tui-idle", "--timeout-ms", localIdleMS); !ok {
		return refuse("claude in %s never went idle; kept it and worktree %s", w.terminal, w.worktree)
	}
	return nil
}
