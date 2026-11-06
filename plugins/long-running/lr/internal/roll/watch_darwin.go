package roll

import (
	"context"
	"errors"
	"os"

	"golang.org/x/sys/unix"
)

// watch reads the directory's own kqueue writes because fsnotify's kqueue
// backend follows a symlink it lists and reports only names it has not seen, so
// a rename over the install link reaches it as no event at all.
func watch(ctx context.Context, dir string, changed func()) error {
	fd, err := unix.Open(dir, unix.O_EVTONLY|unix.O_CLOEXEC, 0)
	if err != nil {
		return err
	}
	defer func() { _ = unix.Close(fd) }()
	kq, err := unix.Kqueue()
	if err != nil {
		return err
	}
	defer func() { _ = unix.Close(kq) }()
	woken, wake, err := os.Pipe()
	if err != nil {
		return err
	}
	defer func() { _ = woken.Close() }()
	stop := context.AfterFunc(ctx, func() { _ = wake.Close() })
	defer func() {
		if stop() {
			_ = wake.Close()
		}
	}()
	var events [2]unix.Kevent_t
	unix.SetKevent(&events[0], fd, unix.EVFILT_VNODE, unix.EV_ADD|unix.EV_CLEAR)
	events[0].Fflags = unix.NOTE_WRITE
	unix.SetKevent(&events[1], int(woken.Fd()), unix.EVFILT_READ, unix.EV_ADD)
	if _, err := unix.Kevent(kq, events[:], nil, nil); err != nil {
		return err
	}
	changed()
	for {
		n, err := unix.Kevent(kq, nil, events[:], nil)
		if errors.Is(err, unix.EINTR) {
			continue
		}
		if err != nil {
			return err
		}
		for _, event := range events[:n] {
			if event.Ident == uint64(woken.Fd()) {
				return nil
			}
		}
		changed()
	}
}
