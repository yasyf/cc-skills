package orca

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	hostSpriteStub = `#!/bin/sh
[ "$1" = exec ] || exit 2
shift
while [ "$1" != -- ]; do
  case $1 in
  -s) box=$2; shift 2 ;;
  --no-stdin) exec < /dev/null; shift ;;
  --file) cp "${2%%:*}" "${2#*:}"; shift 2 ;;
  *) exit 2 ;;
  esac
done
shift
if [ "$box" = sponsor ]; then
  echo service-key
  exit 0
fi
cd "$BOX_HOME"
HOME=$BOX_HOME exec "$@"
`
	hostServicesStub = `#!/bin/sh
file=$HOME/.services.json
[ -f "$file" ] || echo '[]' > "$file"
case "$1 $2" in
"services list") cat "$file" ;;
"services create")
  name=$3
  shift 3
  while [ $# -gt 0 ]; do
    case $1 in
    --cmd) cmd=$2; shift 2 ;;
    --args) args=$2; shift 2 ;;
    *) shift ;;
    esac
  done
  jq --arg name "$name" '. + [{name: $name}]' "$file" > "$file.new" && mv "$file.new" "$file"
  if [ "$name" = store-seed ]; then
    IFS=,
    set -f
    set -- $args
    unset IFS
    "$cmd" "$@"
  fi
  ;;
"services delete") jq --arg name "$3" 'map(select(.name != $name))' "$file" > "$file.new" && mv "$file.new" "$file" ;;
esac
`
	hostMacCCIStub = `#!/bin/sh
case "$1 $2" in
"relay status") echo '[{"mesh":"default","url":"https://relay.test","routes":["brook"]}]' ;;
"node --json") echo '{"public_key":"owner-key"}' ;;
"relay export") echo records ;;
*) exit 2 ;;
esac
`
	hostBoxCCIStub = `#!/bin/sh
case "$1 $2" in
"relay status") if [ -e "$HOME/.relay" ]; then cat "$HOME/.relay"; else echo '[]'; fi ;;
"relay join") [ "$CCI_SERVICE_KEY" = service-key ] || exit 3; echo '[{"mesh":"default","routes":["brook"]}]' > "$HOME/.relay" ;;
"relay seed") cat > "$HOME/.records" ;;
*) exit 2 ;;
esac
`
)

type hostWorld struct {
	t        *testing.T
	box, mac string
	stubs    string
	installs int
	calls    [][]string
}

func (w *hostWorld) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	w.calls = append(w.calls, c.Argv)
	if slices.Contains(c.Argv, installTools) {
		w.installs++
		return shell.Result{Stdout: "installed cci 0.49.36\ninstalled lr 9.9.9\ninstalled ccx " + rollCCX + "\ninstalled long-running@skills " + rollPack + "\n"}, nil
	}
	if stub := filepath.Join(w.stubs, c.Argv[0]); exists(stub) {
		c.Argv = slices.Concat([]string{stub}, c.Argv[1:])
	}
	c.Env = append(c.Env, "PATH="+w.stubs+":"+os.Getenv("PATH"), "BOX_HOME="+w.box, "GIT_CONFIG_NOSYSTEM=1")
	return shell.Exec{}.Run(ctx, c)
}

func (w *hostWorld) write(path, text string, mode os.FileMode) {
	w.t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		w.t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), mode); err != nil {
		w.t.Fatal(err)
	}
}

func (w *hostWorld) snapshot() map[string]string {
	w.t.Helper()
	root, err := os.OpenRoot(w.box)
	if err != nil {
		w.t.Fatal(err)
	}
	defer func() { _ = root.Close() }()
	box := root.FS()
	seen := map[string]string{}
	err = fs.WalkDir(box, ".", func(path string, entry fs.DirEntry, err error) error {
		if err != nil || entry.IsDir() {
			return err
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		state := info.Mode().String() + " " + info.ModTime().Format(time.RFC3339Nano)
		if entry.Type()&fs.ModeSymlink != 0 {
			target, err := fs.ReadLink(box, path)
			if err != nil {
				return err
			}
			state += " -> " + target
		} else {
			raw, err := fs.ReadFile(box, path)
			if err != nil {
				return err
			}
			sum := sha256.Sum256(raw)
			state += " " + hex.EncodeToString(sum[:])
		}
		seen[path] = state
		return nil
	})
	if err != nil {
		w.t.Fatal(err)
	}
	return seen
}

func TestSpritesHostSetsUpAHostAndASecondRunChangesNothing(t *testing.T) {
	box, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	w := &hostWorld{t: t, box: box, mac: t.TempDir(), stubs: t.TempDir()}
	for name, text := range map[string]string{"sprite": hostSpriteStub, "sprite-env": hostServicesStub, "cci": hostMacCCIStub, "ccx": "#!/bin/sh\necho app-token\n", "sudo": "#!/bin/sh\n[ \"$1\" != -n ] || shift\nexec \"$@\"\n", "aws": "#!/bin/sh\n"} {
		w.write(filepath.Join(w.stubs, name), text, 0o755)
	}
	w.write(filepath.Join(box, ".local", "bin", "cci"), hostBoxCCIStub, 0o755)
	origin := filepath.Join(w.mac, "origin")
	for _, argv := range [][]string{{"init", "--quiet", "-b", "dev", origin}, {"-C", origin, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "--allow-empty", "-m", "first"}} {
		if said, err := exec.Command("git", argv...).CombinedOutput(); err != nil {
			t.Fatal(string(said))
		}
	}
	w.write(filepath.Join(box, ".gitconfig"), "[url \"file://"+origin+"\"]\n\tinsteadOf = https://github.com/o/r.git\n", 0o644)
	plugin := filepath.Join(w.mac, "plugin")
	w.write(filepath.Join(plugin, "bin", "lr.binrun"), lrPin("linux-x86_64"), 0o700)
	t.Setenv("BINRUN_PLUGIN_ROOT", plugin)
	state := filepath.Join(w.mac, "state")
	w.write(filepath.Join(state, "runner.json"), `{"host":{"sprite":"desk-host","aws_profiles":["core-auto","release-operator"],"github_app":{"name":"drive-bot","client_id":"Iv1.test","private_key_command":["lr","desk-creds","get","secret/APP_KEY","Value"]}},"landing":{"repo":"o/r"},"orca":{"sprite":{"env":{"WORKER_CCI_URL":"`+testCCIURL+`","WORKER_CCI_SHA256":"`+testCCISHA+`","WORKER_RELAY_SPONSOR":"sponsor"},"tools":`+rollTools+`}}}`, 0o600)
	macHome := filepath.Join(box, "Users", "me")
	w.write(filepath.Join(state, "lr.json"), `{"drive":"brook","repo":"o/r","checkout":"`+filepath.Join(macHome, "worktrees", "runner")+`","movers":["lanes"]}`, 0o600)
	s, err := store.Open(filepath.Join(w.mac, "lr.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	var out bytes.Buffer
	maker := HostMaker{Runner: w, Store: s, State: state, Config: filepath.Join(state, "lr.json"), CCIDrive: "brook", MacHome: macHome, Tmp: filepath.Join(box, "tmp"), VarTmp: filepath.Join(box, "var", "tmp"), Sleep: func(time.Duration) {}, Now: func() time.Time { return time.Date(2026, 11, 8, 8, 0, 0, 0, time.UTC) }, Out: &out}
	if err := maker.Make(context.Background(), "standby"); err != nil {
		t.Fatal(err, out.String())
	}
	st := filepath.Join(box, ".claude", "scratch", "brook-landing")
	for file, want := range map[string]string{
		filepath.Join(st, "lr.json"):                            `"queue_key": "` + hostLR + ` desk-creds get secret/TRUNK_MERGE_API_KEY Value"`,
		filepath.Join(st, "runner.json"):                        `"sprite": "standby"`,
		filepath.Join(st, "bk.json"):                            `"secret/BUILDKITE_TOKEN"`,
		filepath.Join(st, "run.sh"):                             "supervisor run --config " + hostHome + "/.claude/scratch/brook-landing/lr.json",
		filepath.Join(st, "bin", "gh"):                          "desk-creds get secret/GH_TOKEN Value",
		filepath.Join(box, ".aws", "config"):                    "[profile release-operator]\ncredential_process = curl -sSf http://127.0.0.1:9181/release-operator",
		filepath.Join(box, ".config", "ccx", "github-app.toml"): `private_key_command = ["lr","desk-creds","get","secret/APP_KEY","Value"]`,
		filepath.Join(box, ".gitconfig"):                        "helper = !" + st + "/bin/gh auth git-credential",
		filepath.Join(box, ".services.json"):                    `"desk-creds"`,
		filepath.Join(box, ".relay"):                            "default",
		filepath.Join(box, ".records"):                          "records",
		filepath.Join(st, "records-seeded"):                     "",
		filepath.Join(box, "tmp", hostRunner, ".git", "HEAD"):   "ref: refs/heads/dev",
	} {
		raw, err := os.ReadFile(file)
		if err != nil || !strings.Contains(string(raw), want) {
			t.Fatalf("%s holds %q, want %q (%v)", file, raw, want, err)
		}
	}
	if got, err := os.Readlink(macHome); err != nil || got != box {
		t.Fatalf("the Mac's home links to %q, not the box's home (%v)", got, err)
	}
	if got, err := os.Readlink(filepath.Join(box, "worktrees", "runner")); err != nil || got != filepath.Join(box, "tmp", hostRunner) {
		t.Fatalf("the drive's checkout links to %q (%v)", got, err)
	}
	if w.installs != 1 {
		t.Fatalf("the pins installed %d times", w.installs)
	}
	before, calls := w.snapshot(), len(w.calls)
	out.Reset()
	if err := maker.Make(context.Background(), "standby"); err != nil {
		t.Fatal(err, out.String())
	}
	after := w.snapshot()
	for path, was := range before {
		if after[path] != was {
			t.Errorf("the second run changed %s: %q became %q", path, was, after[path])
		}
	}
	for path, is := range after {
		if _, held := before[path]; !held {
			t.Errorf("the second run made %s: %q", path, is)
		}
	}
	if w.installs != 1 {
		t.Fatalf("the second run installed the pins again")
	}
	if rerun := w.calls[calls:]; len(rerun) != 1 || !slices.Contains(rerun[0], hostLayout) {
		t.Fatalf("the second run ran more than the layout: %q", rerun)
	}
	if strings.Contains(out.String(), "wrote") || strings.Contains(out.String(), "linked") {
		t.Fatalf("the second run says it changed something: %s", out.String())
	}
}
