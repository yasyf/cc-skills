from __future__ import annotations

import array
import ctypes
import errno
import fcntl
import os
import resource
import select
import struct
import sys
import termios
from dataclasses import dataclass


class WatchLost(RuntimeError):
    pass


@dataclass(frozen=True)
class Event:
    token: int
    authority: bool = False
    retired: bool = False
    name: str | None = None
    structural: bool = True


class Kevent(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("ident", ctypes.c_size_t),
        ("filter", ctypes.c_int16),
        ("flags", ctypes.c_uint16),
        ("fflags", ctypes.c_uint32),
        ("data", ctypes.c_ssize_t),
        ("udata", ctypes.c_void_p),
    ]


class Timespec(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]


class Kqueue:
    RECEIPT = 0x0040

    def __init__(self):
        self.libc = ctypes.CDLL(None, use_errno=True)
        maximum = ctypes.c_int()
        size = ctypes.c_size_t(ctypes.sizeof(maximum))
        self.libc.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        self.libc.sysctlbyname.restype = ctypes.c_int
        if self.libc.sysctlbyname(b"kern.maxfilesperproc", ctypes.byref(maximum), ctypes.byref(size), None, 0) < 0:
            raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = maximum.value if hard == resource.RLIM_INFINITY else min(hard, maximum.value)
        if soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
        self.queue = select.kqueue()
        self.libc.kevent.argtypes = [ctypes.c_int, ctypes.POINTER(Kevent), ctypes.c_int, ctypes.POINTER(Kevent), ctypes.c_int, ctypes.POINTER(Timespec)]
        self.libc.kevent.restype = ctypes.c_int
        self.capacity = 1
        self.buffer = (Kevent * self.capacity)()
        self.count = 0
        self.next_token = 0
        self.zero = Timespec()

    def add(self, fd: int, path: str) -> int:
        self.next_token += 1
        token = self.next_token
        mask = select.KQ_NOTE_WRITE | select.KQ_NOTE_DELETE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_ATTRIB | select.KQ_NOTE_LINK | select.KQ_NOTE_RENAME | select.KQ_NOTE_REVOKE
        change = Kevent(fd, select.KQ_FILTER_VNODE, select.KQ_EV_ADD | select.KQ_EV_CLEAR | self.RECEIPT, mask, 0, token)
        receipt = Kevent()
        result = self.libc.kevent(self.queue.fileno(), ctypes.byref(change), 1, ctypes.byref(receipt), 1, ctypes.byref(self.zero))
        if result < 0:
            raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()), path)
        if result != 1 or not receipt.flags & select.KQ_EV_ERROR:
            raise WatchLost("missing vnode registration receipt")
        if receipt.data:
            raise OSError(receipt.data, os.strerror(receipt.data), path)
        self.count += 1
        if self.count > self.capacity:
            self.capacity *= 2
            self.buffer = (Kevent * self.capacity)()
        return token

    def remove(self, token: int, fd: int) -> None:
        os.close(fd)
        self.count -= 1

    def cut(self) -> list[Event]:
        assert self.capacity >= self.count
        count = self.libc.kevent(self.queue.fileno(), None, 0, self.buffer, self.capacity, ctypes.byref(self.zero))
        if count < 0:
            raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
        result = []
        for position in range(count):
            event = self.buffer[position]
            if event.flags & select.KQ_EV_ERROR:
                raise WatchLost(f"vnode event error: {event.data}")
            authority = event.fflags & (select.KQ_NOTE_ATTRIB | select.KQ_NOTE_REVOKE | select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME)
            result.append(Event(event.udata, bool(authority), bool(event.fflags & select.KQ_NOTE_REVOKE)))
        return result

    def close(self) -> None:
        self.queue.close()


class Inotify:
    MASK = 0x00000002 | 0x00000004 | 0x00000008 | 0x00000040 | 0x00000080 | 0x00000100 | 0x00000200 | 0x00000400 | 0x00000800
    OVERFLOW = 0x00004000
    IGNORED = 0x00008000

    def __init__(self):
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.inotify_init1.argtypes = [ctypes.c_int]
        self.libc.inotify_init1.restype = ctypes.c_int
        self.libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        self.libc.inotify_add_watch.restype = ctypes.c_int
        self.libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
        self.libc.inotify_rm_watch.restype = ctypes.c_int
        self.fd = self.libc.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
        self.tokens = {}
        self.watches = {}
        self.next_token = 0
        self.pending = []
        self.lost = False

    def capture(self) -> None:
        available = array.array("i", [0])
        fcntl.ioctl(self.fd, termios.FIONREAD, available, True)
        remaining = available[0]
        while remaining > 0:
            data = os.read(self.fd, max(remaining, 4096))
            remaining -= len(data)
            offset = 0
            while offset < len(data):
                wd, mask, _, length = struct.unpack_from("iIII", data, offset)
                name = os.fsdecode(data[offset + 16:offset + 16 + length].split(b"\0", 1)[0]) if length else None
                offset += 16 + length
                if mask & self.OVERFLOW:
                    self.lost = True
                elif wd in self.tokens:
                    token = self.tokens[wd]
                    authority = name is None and bool(mask & (0x00000004 | 0x00000400 | 0x00000800 | self.IGNORED))
                    structural = bool(mask & (0x00000040 | 0x00000080 | 0x00000100 | 0x00000200))
                    self.pending.append(Event(token, authority, bool(mask & self.IGNORED), name, structural))
                    if mask & self.IGNORED:
                        del self.tokens[wd]
        if self.lost:
            raise WatchLost("inotify queue overflow; roster generation invalid")

    def add(self, fd: int, path: str) -> int:
        self.capture()
        wd = self.libc.inotify_add_watch(self.fd, os.fsencode(f"/proc/self/fd/{fd}"), self.MASK)
        if wd < 0:
            raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()), path)
        self.capture()
        if wd in self.tokens:
            raise WatchLost("inotify reused a live watch descriptor")
        self.next_token += 1
        token = self.next_token
        self.tokens[wd] = token
        self.watches[token] = wd
        return token

    def remove(self, token: int, fd: int) -> None:
        wd = self.watches.pop(token)
        if self.tokens.get(wd) == token:
            result = self.libc.inotify_rm_watch(self.fd, wd)
            if result < 0 and ctypes.get_errno() != errno.EINVAL:
                raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
            self.capture()
        os.close(fd)

    def cut(self) -> list[Event]:
        self.capture()
        events, self.pending = self.pending, []
        return events

    def close(self) -> None:
        os.close(self.fd)


def native_events() -> Kqueue | Inotify:
    if sys.platform == "darwin":
        return Kqueue()
    if sys.platform == "linux":
        return Inotify()
    raise RuntimeError(f"native roster watches unavailable on {sys.platform}")
