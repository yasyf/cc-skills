package install

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
)

const pluginLink = "current"

var (
	wholeVersion = regexp.MustCompile(`^\d+(\.\d+)*$`)
	versionPart  = regexp.MustCompile(`\d+`)
)

func versionKey(version string) []int {
	before, _, _ := strings.Cut(version, "-")
	parts := versionPart.FindAllString(before, -1)
	key := make([]int, 0, len(parts))
	for _, part := range parts {
		number, err := strconv.Atoi(part)
		if err != nil {
			continue
		}
		key = append(key, number)
	}
	return key
}

func link(claude string) string { return filepath.Join(claude, pluginName, pluginLink) }

func Installed(claude string) string {
	resolved, err := filepath.EvalSymlinks(link(claude))
	if err != nil {
		return ""
	}
	return filepath.Base(resolved)
}

func InstalledSince(claude string) (string, time.Time, error) {
	moved, err := os.Lstat(link(claude))
	if err != nil {
		return "", time.Time{}, err
	}
	return Installed(claude), moved.ModTime(), nil
}

func Promote(claude, target string) error {
	version := filepath.Base(target)
	if !wholeVersion.MatchString(version) {
		return fmt.Errorf("%s is no version directory", target)
	}
	if named := Installed(claude); named != "" && slices.Compare(versionKey(named), versionKey(version)) >= 0 {
		return nil
	}
	path := link(claude)
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	staged := fmt.Sprintf("%s.%d.%d", path, os.Getpid(), time.Now().UnixNano())
	if err := os.Symlink(target, staged); err != nil {
		return err
	}
	return os.Rename(staged, path)
}
