package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"

	"github.com/yasyf/daemonkit/durable"
)

const selfImageLockDeadline = 5 * time.Minute

func retainSelfImage(source *os.File) (string, error) {
	info, err := source.Stat()
	if err != nil {
		return "", err
	}
	digest, err := executableDigest(source)
	if err != nil {
		return "", err
	}
	generation := fmt.Sprintf("%d-%o", info.ModTime().UnixNano(), info.Mode().Perm())
	dir := filepath.Join(appPaths().StateDir(), "self-images", digest, generation)
	target := filepath.Join(dir, "codex-ask")
	if present, err := verifySelfImage(target, info, digest); err != nil || present {
		return target, err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	ctx, cancel := context.WithTimeout(context.Background(), selfImageLockDeadline)
	defer cancel()
	lock, err := durable.AcquireLock(ctx, filepath.Join(dir, ".lock"))
	if err != nil {
		return "", err
	}
	defer lock.Close()
	if present, err := verifySelfImage(target, info, digest); err != nil || present {
		return target, err
	}
	stage, err := os.MkdirTemp(dir, ".stage-")
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(stage)
	staged := filepath.Join(stage, "codex-ask")
	writer, err := durable.Create(staged, 0o600)
	if err != nil {
		return "", err
	}
	defer writer.Close()
	if _, err := source.Seek(0, io.SeekStart); err != nil {
		return "", err
	}
	copied := sha256.New()
	if _, err := io.Copy(io.MultiWriter(writer, copied), source); err != nil {
		return "", err
	}
	if hex.EncodeToString(copied.Sum(nil)) != digest {
		return "", fmt.Errorf("source self image changed while retaining %s", source.Name())
	}
	if err := writer.Commit(); err != nil {
		return "", err
	}
	file, err := os.OpenFile(staged, os.O_RDWR, 0)
	if err != nil {
		return "", err
	}
	err = file.Chmod(info.Mode().Perm())
	if err == nil {
		err = os.Chtimes(staged, info.ModTime(), info.ModTime())
	}
	if err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err != nil {
		return "", err
	}
	if closeErr != nil {
		return "", closeErr
	}
	if err := durable.Rename(staged, target); err != nil {
		return "", err
	}
	return target, nil
}

func verifySelfImage(path string, source os.FileInfo, digest string) (bool, error) {
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm() != source.Mode().Perm() || !info.ModTime().Equal(source.ModTime()) {
		return false, fmt.Errorf("retained self image metadata mismatch: %s", path)
	}
	file, err := os.Open(path)
	if err != nil {
		return false, err
	}
	defer file.Close()
	actual, err := executableDigest(file)
	if err != nil {
		return false, err
	}
	if actual != digest {
		return false, fmt.Errorf("retained self image checksum mismatch: %s", path)
	}
	return true, nil
}

func executableDigest(file *os.File) (string, error) {
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		return "", err
	}
	digest := sha256.New()
	if _, err := io.Copy(digest, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(digest.Sum(nil)), nil
}
