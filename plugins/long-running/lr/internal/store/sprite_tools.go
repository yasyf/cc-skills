package store

import (
	"database/sql"
	"errors"
	"time"
)

type SpriteTool struct {
	Sprite  string
	Name    string
	Version string
	At      time.Time
}

func (s *Store) RecordSpriteTools(sprite string, installed map[string]string, at time.Time) error {
	for name, version := range installed {
		if _, err := s.db.Exec("INSERT INTO sprite_tools (sprite, name, version, at) VALUES (?, ?, ?, ?) ON CONFLICT (sprite, name) DO UPDATE SET version = excluded.version, at = excluded.at", sprite, name, version, Stamp(at)); err != nil {
			return err
		}
	}
	return nil
}

func (s *Store) SpriteTools() ([]SpriteTool, error) {
	rows, err := s.db.Query("SELECT sprite, name, version, at FROM sprite_tools ORDER BY sprite, name")
	return collect(rows, err, func(rows *sql.Rows) (SpriteTool, error) {
		var tool SpriteTool
		err := rows.Scan(&tool.Sprite, &tool.Name, &tool.Version, moment{&tool.At})
		return tool, err
	})
}

func (s *Store) MarkBadTool(name, version, why string, at time.Time) error {
	_, err := s.db.Exec("INSERT OR IGNORE INTO bad_tools (name, version, why, at) VALUES (?, ?, ?, ?)", name, version, why, Stamp(at))
	return err
}

func (s *Store) BadTool(name, version string) (string, error) {
	var why string
	err := s.db.QueryRow("SELECT why FROM bad_tools WHERE name = ? AND version = ?", name, version).Scan(&why)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	return why, err
}
