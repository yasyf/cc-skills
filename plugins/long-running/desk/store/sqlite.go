package store

import (
	"context"
	"database/sql/driver"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"strings"
	"syscall"
	"unicode/utf8"

	"modernc.org/sqlite"

	"github.com/yasyf/cc-skills/plugins/long-running/desk/pyjson"
)

const connectTimeout = "PRAGMA busy_timeout = 5000"

type conn struct {
	raw     driver.Conn
	execer  driver.ExecerContext
	queryer driver.QueryerContext
	closed  bool
}

func dial(ctx context.Context, path, mode string) (*conn, error) {
	location, err := absolute(path)
	if err != nil {
		return nil, err
	}
	raw, err := (&sqlite.Driver{}).Open(fileURI(location) + "?mode=" + mode)
	if err != nil {
		return nil, err
	}
	c := &conn{raw: raw, execer: raw.(driver.ExecerContext), queryer: raw.(driver.QueryerContext)}
	if err := c.exec(ctx, connectTimeout); err != nil {
		return nil, errors.Join(err, c.close())
	}
	return c, nil
}

func dialWriter(ctx context.Context, path string) (*conn, error) {
	c, err := dial(ctx, path, "rw")
	if err != nil {
		return nil, err
	}
	version, err := c.integer(ctx, "PRAGMA user_version")
	if err == nil && version != 2 {
		err = &ValueError{Message: "unsupported action store version: " + path}
	}
	if err == nil {
		err = c.exec(ctx, "PRAGMA synchronous = FULL")
	}
	if err != nil {
		return nil, errors.Join(err, c.close())
	}
	return c, nil
}

func (c *conn) close() error {
	if c.closed {
		return nil
	}
	c.closed = true
	return c.raw.Close()
}

func (c *conn) exec(ctx context.Context, query string, args ...any) error {
	named, err := bind(args)
	if err != nil {
		return err
	}
	_, err = c.execer.ExecContext(ctx, query, named)
	return err
}

func (c *conn) rows(ctx context.Context, query string, args ...any) ([][]driver.Value, error) {
	return c.fetch(ctx, -1, query, args)
}

func (c *conn) row(ctx context.Context, query string, args ...any) ([]driver.Value, error) {
	rows, err := c.fetch(ctx, 1, query, args)
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return rows[0], nil
}

func (c *conn) integer(ctx context.Context, query string, args ...any) (int64, error) {
	row, err := c.row(ctx, query, args...)
	if err != nil {
		return 0, err
	}
	if row == nil {
		return 0, fmt.Errorf("%s returned no row", query)
	}
	return asInteger(row[0])
}

func (c *conn) fetch(ctx context.Context, limit int, query string, args []any) (selected [][]driver.Value, err error) {
	named, err := bind(args)
	if err != nil {
		return nil, err
	}
	rows, err := c.queryer.QueryContext(ctx, query, named)
	if err != nil {
		return nil, err
	}
	defer func() { err = errors.Join(err, rows.Close()) }()
	for limit < 0 || len(selected) < limit {
		values := make([]driver.Value, len(rows.Columns()))
		if err := rows.Next(values); errors.Is(err, io.EOF) {
			break
		} else if err != nil {
			return nil, err
		}
		for _, value := range values {
			if text, ok := value.(string); ok && !utf8.ValidString(text) {
				return nil, fmt.Errorf("Could not decode to UTF-8 column with text %q", text)
			}
		}
		selected = append(selected, values)
	}
	return selected, nil
}

func bind(args []any) ([]driver.NamedValue, error) {
	named := make([]driver.NamedValue, len(args))
	for index, arg := range args {
		switch value := arg.(type) {
		case string:
			if err := encodable(value); err != nil {
				return nil, err
			}
		case int:
			arg = int64(value)
		case int64, nil:
		default:
			return nil, fmt.Errorf("store binds no %T", value)
		}
		named[index] = driver.NamedValue{Ordinal: index + 1, Value: arg}
	}
	return named, nil
}

func encodable(text string) error {
	if utf8.ValidString(text) {
		return nil
	}
	position := 0
	for index := 0; index < len(text); position++ {
		r, size := pyjson.DecodeRune(text[index:])
		if size == 0 {
			return &ValueError{Message: fmt.Sprintf("text %q is not UTF-8 at byte %d", text, index)}
		}
		if r >= 0xd800 && r <= 0xdfff {
			return &ValueError{Message: fmt.Sprintf("'utf-8' codec can't encode character '\\u%04x' in position %d: surrogates not allowed", r, position)}
		}
		index += size
	}
	return nil
}

func nullable(text *string) any {
	if text == nil {
		return nil
	}
	return *text
}

func asText(value driver.Value) (string, error) {
	if text, ok := value.(string); ok {
		return text, nil
	}
	return "", fmt.Errorf("column holds %T, not TEXT", value)
}

func asOptionalText(value driver.Value) (*string, error) {
	if value == nil {
		return nil, nil
	}
	text, err := asText(value)
	return &text, err
}

func asInteger(value driver.Value) (int64, error) {
	if number, ok := value.(int64); ok {
		return number, nil
	}
	return 0, fmt.Errorf("column holds %T, not INTEGER", value)
}

func pyPath(path string) string {
	prefix := ""
	switch {
	case strings.HasPrefix(path, "//") && !strings.HasPrefix(path, "///"):
		prefix = "//"
	case strings.HasPrefix(path, "/"):
		prefix = "/"
	}
	var parts []string
	for _, part := range strings.Split(path, "/") {
		if part != "" && part != "." {
			parts = append(parts, part)
		}
	}
	if joined := prefix + strings.Join(parts, "/"); joined != "" {
		return joined
	}
	return "."
}

func joinPath(root, name string) string {
	switch {
	case root == ".":
		return name
	case strings.HasSuffix(root, "/"):
		return root + name
	}
	return root + "/" + name
}

func absolute(path string) (string, error) {
	if strings.HasPrefix(path, "/") {
		return path, nil
	}
	cwd, err := os.Getwd()
	if err != nil {
		return "", err
	}
	return joinPath(cwd, path), nil
}

func fileURI(path string) string {
	var out strings.Builder
	out.WriteString("file://")
	for index := 0; index < len(path); index++ {
		c := path[index]
		if c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || strings.IndexByte("_.-~/", c) >= 0 {
			out.WriteByte(c)
			continue
		}
		fmt.Fprintf(&out, "%%%02X", c)
	}
	return out.String()
}

func isFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

type fileIdentity struct {
	device uint64
	inode  uint64
}

func identify(path string) (fileIdentity, error) {
	info, err := os.Stat(path)
	if err != nil {
		return fileIdentity{}, err
	}
	state := info.Sys().(*syscall.Stat_t)
	return fileIdentity{device: uint64(state.Dev), inode: uint64(state.Ino)}, nil
}

func retryInterrupted(call func() error) error {
	for {
		if err := call(); !errors.Is(err, syscall.EINTR) {
			return err
		}
	}
}

func flock(file *os.File) error {
	return retryInterrupted(func() error { return syscall.Flock(int(file.Fd()), syscall.LOCK_EX) })
}

// fsync is os.fsync's plain fsync(2): os.File.Sync is F_FULLFSYNC on darwin.
func fsync(path string) (err error) {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, file.Close()) }()
	return retryInterrupted(func() error { return syscall.Fsync(int(file.Fd())) })
}

func removeStaged(path string) error {
	if err := os.Remove(path); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	return nil
}
