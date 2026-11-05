package config

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

func SetRunnerKey(path, key, value string) error {
	var parsed any = value
	if json.Valid([]byte(value)) {
		decoder := json.NewDecoder(strings.NewReader(value))
		decoder.UseNumber()
		if err := decoder.Decode(&parsed); err != nil {
			return err
		}
	}
	return editRunner(path, func(whole map[string]any) error {
		parents, last := splitKey(key)
		object := whole
		for i, name := range parents {
			next, found := object[name]
			if !found {
				next = map[string]any{}
				object[name] = next
			}
			child, isObject := next.(map[string]any)
			if !isObject {
				return fmt.Errorf("%s is not an object, so %s cannot be set", strings.Join(parents[:i+1], "."), key)
			}
			object = child
		}
		object[last] = parsed
		return nil
	})
}

func UnsetRunnerKey(path, key string) error {
	return editRunner(path, func(whole map[string]any) error {
		parents, last := splitKey(key)
		object := whole
		for _, name := range parents {
			child, isObject := object[name].(map[string]any)
			if !isObject {
				return fmt.Errorf("%s is not set", key)
			}
			object = child
		}
		if _, found := object[last]; !found {
			return fmt.Errorf("%s is not set", key)
		}
		delete(object, last)
		return nil
	})
}

func splitKey(key string) ([]string, string) {
	names := strings.Split(key, ".")
	return names[:len(names)-1], names[len(names)-1]
}

func editRunner(path string, edit func(map[string]any) error) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	info, err := os.Stat(path)
	if err != nil {
		return err
	}
	var whole map[string]any
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&whole); err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	if err := edit(whole); err != nil {
		return err
	}
	var body bytes.Buffer
	encoder := json.NewEncoder(&body)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(whole); err != nil {
		return err
	}
	staged, err := os.CreateTemp(filepath.Dir(path), filepath.Base(path)+".*.new")
	if err != nil {
		return err
	}
	defer func() { _ = os.Remove(staged.Name()) }()
	if _, err := staged.Write(body.Bytes()); err != nil {
		return err
	}
	if err := staged.Chmod(info.Mode()); err != nil {
		return err
	}
	if err := staged.Close(); err != nil {
		return err
	}
	if _, err := LoadRunner(staged.Name()); err != nil {
		return fmt.Errorf("the edit leaves runner.json unloadable, so nothing was written: %w", err)
	}
	return os.Rename(staged.Name(), path)
}
