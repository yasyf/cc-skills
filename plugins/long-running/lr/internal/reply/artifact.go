package reply

import (
	"bytes"
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/arch"
)

const (
	maxArtifacts   = 5
	kindCode       = "code"
	kindTranscript = "transcript"
	kindImage      = "image"
)

var (
	artifactKinds = []string{kindCode, kindTranscript, kindImage}
	transcriptWho = []string{"user", "assistant"}
	imageTypes    = []string{"image/png", "image/jpeg", "image/gif", "image/webp"}
)

type Artifact struct {
	Title    string `json:"title"`
	Kind     string `json:"kind"`
	File     string `json:"file,omitempty"`
	Language string `json:"language,omitempty"`
	URL      string `json:"url,omitempty"`
	Size     string `json:"size,omitempty"`
	Image    string `json:"image,omitempty"`
}

func (a Artifact) wellFormed() bool {
	return strings.TrimSpace(a.Title) != "" && slices.Contains(artifactKinds, a.Kind) && filepath.IsAbs(a.File)
}

func (a Artifact) measured(body []byte) (string, error) {
	if len(bytes.TrimSpace(body)) == 0 {
		return "", errors.New("is empty")
	}
	if a.Kind == kindImage {
		if !slices.Contains(imageTypes, http.DetectContentType(body)) {
			return "", errors.New("is not a PNG, JPEG, GIF or WebP image")
		}
		return "", nil
	}
	if a.Kind == kindCode {
		return counted(len(bytes.Split(bytes.TrimRight(body, "\n"), []byte("\n"))), "line"), nil
	}
	turns := 0
	for line := range bytes.Lines(body) {
		var event struct {
			Kind string `json:"kind"`
		}
		if err := json.Unmarshal(line, &event); err != nil {
			return "", fmt.Errorf("is not cc-transcript show --json: %w", err)
		}
		if slices.Contains(transcriptWho, event.Kind) {
			turns++
		}
	}
	if turns == 0 {
		return "", errors.New("is not cc-transcript show --json: it has no turns")
	}
	return counted(turns, "turn"), nil
}

func counted(n int, noun string) string {
	if n == 1 {
		return "1 " + noun
	}
	return strconv.Itoa(n) + " " + noun + "s"
}

func (f Fanout) withArtifacts(ctx context.Context, r Reply) (Reply, string) {
	dir := filepath.Join(f.Entry.StateDir, storeDir, r.ID)
	type read struct {
		artifact Artifact
		body     []byte
	}
	var warned []string
	found := make([]read, 0, len(r.Artifacts))
	for _, a := range r.Artifacts {
		body, err := os.ReadFile(a.File)
		if err == nil {
			a.Size, err = a.measured(body)
		}
		if err != nil {
			warned = append(warned, fmt.Sprintf("artifact %q was left off: %s %v", a.Title, a.File, err))
			continue
		}
		a.Title = shortened(strings.Join(strings.Fields(a.Title), " "), maxTitle)
		if a.Kind == kindCode {
			a.Language = cmp.Or(a.Language, strings.TrimPrefix(filepath.Ext(a.File), "."))
		}
		found = append(found, read{a, body})
	}
	r.Artifacts = nil
	if err := os.RemoveAll(dir); err != nil {
		return r, strings.Join(append(warned, "the earlier artifacts were not removed: "+err.Error()), "; ")
	}
	if len(found) == 0 {
		return r, strings.Join(warned, "; ")
	}
	base, err := arch.DashboardLink(ctx, f.Runner, f.Entry.StateDir)
	if err == nil {
		err = os.MkdirAll(dir, 0o750)
	}
	if err != nil {
		return r, strings.Join(append(warned, "no artifact was attached: "+err.Error()), "; ")
	}
	for _, one := range found {
		n := len(r.Artifacts)
		copied := filepath.Join(dir, strconv.Itoa(n))
		if err := os.WriteFile(copied, one.body, 0o600); err != nil {
			warned = append(warned, fmt.Sprintf("artifact %q was left off: %v", one.artifact.Title, err))
			continue
		}
		one.artifact.File = ""
		if one.artifact.Kind == kindImage {
			one.artifact.Image = copied
		}
		one.artifact.URL = fmt.Sprintf("%sartifact/%s/%d", base, r.ID, n)
		r.Artifacts = append(r.Artifacts, one.artifact)
	}
	return r, strings.Join(warned, "; ")
}

func (r Reply) artifactsLinked() Reply {
	for _, a := range r.Artifacts {
		r.MD += "\n\n[" + a.Title + "](" + a.URL + ")"
	}
	return r
}
