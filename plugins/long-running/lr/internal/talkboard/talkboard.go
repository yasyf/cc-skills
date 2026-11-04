package talkboard

import (
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

const (
	Section   = "talk"
	Links     = "talk.json"
	notesFile = "talk-notes"
)

func Session(entry drive.Entry) string {
	return "talk-" + entry.Drive
}

func Opened(entry drive.Entry) bool {
	_, err := os.Stat(filepath.Join(entry.StateDir, Links))
	return err == nil
}

func LastNote(entry drive.Entry) string {
	notes, err := ReadNotes(entry)
	if err != nil || len(notes) == 0 {
		return ""
	}
	return notes[len(notes)-1]
}

func NotesPath(entry drive.Entry) string {
	return filepath.Join(entry.StateDir, "owner-relay", notesFile)
}

func ReadNotes(entry drive.Entry) ([]string, error) {
	data, err := os.ReadFile(NotesPath(entry))
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return strings.Fields(string(data)), nil
}
