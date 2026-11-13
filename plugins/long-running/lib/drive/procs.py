"""The process table, read in this process through libproc and sysctl on macOS and /proc on Linux, so no question the drive asks about a process starts one."""

import ctypes
import os
import sys
import time
from dataclasses import dataclass
from functools import cache
from pathlib import Path

LSTART = "%a %b %e %H:%M:%S %Y"
PROC_PIDTBSDINFO = 3
PROC_PIDT_SHORTBSDINFO = 13
SZOMB = 5
WITH_ZOMBIES = 1
UNREADABLE = (FileNotFoundError, ProcessLookupError, PermissionError)
CTL_KERN = 1
KERN_ARGMAX = 8
KERN_PROCARGS2 = 49
SPARE_PIDS = 256
STAT_STATE, STAT_PPID, STAT_PGRP, STAT_STARTTIME = 0, 1, 2, 19


@dataclass(frozen=True, slots=True)
class Process:
    pid: int
    ppid: int
    pgid: int
    zombie: bool


class ShortInfo(ctypes.Structure):
    _fields_ = [
        ("pid", ctypes.c_uint32),
        ("ppid", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("comm", ctypes.c_char * 16),
        ("flags", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("ruid", ctypes.c_uint32),
        ("rgid", ctypes.c_uint32),
        ("svuid", ctypes.c_uint32),
        ("svgid", ctypes.c_uint32),
        ("rfu", ctypes.c_uint32),
    ]


class BsdInfo(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("xstatus", ctypes.c_uint32),
        ("pid", ctypes.c_uint32),
        ("ppid", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("ruid", ctypes.c_uint32),
        ("rgid", ctypes.c_uint32),
        ("svuid", ctypes.c_uint32),
        ("svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("comm", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        ("nfiles", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32),
        ("pjobc", ctypes.c_uint32),
        ("tdev", ctypes.c_uint32),
        ("tpgid", ctypes.c_uint32),
        ("nice", ctypes.c_int32),
        ("start_tvsec", ctypes.c_uint64),
        ("start_tvusec", ctypes.c_uint64),
    ]


@cache
def libsystem() -> ctypes.CDLL:
    system = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    system.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
    system.proc_listallpids.restype = ctypes.c_int
    system.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    system.proc_pidinfo.restype = ctypes.c_int
    system.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
    system.sysctl.restype = ctypes.c_int
    return system


def table() -> list[Process]:
    """Every process this user may see, as `ps -A` lists them, with its parent, its process group and whether it is a zombie awaiting its parent."""
    if sys.platform == "darwin":
        return darwin_table()
    return [Process(pid, int(fields[STAT_PPID]), int(fields[STAT_PGRP]), fields[STAT_STATE] == "Z") for pid in proc_pids() if (fields := proc_stat(pid))]


def started(pid: int) -> int | None:
    """When the process holding pid started, in whole seconds since the epoch as `ps -o lstart=` reads it, None when no process holds pid or this user may not read it."""
    if sys.platform == "darwin":
        info = BsdInfo()
        read = libsystem().proc_pidinfo(pid, PROC_PIDTBSDINFO, WITH_ZOMBIES,ctypes.byref(info), ctypes.sizeof(info))
        return info.start_tvsec if read == ctypes.sizeof(info) else None
    if not (fields := proc_stat(pid)):
        return None
    return int(boot_time() + int(fields[STAT_STARTTIME]) / os.sysconf("SC_CLK_TCK"))


def lstart(seconds: int, utc: bool = False) -> str:
    """A start time as `ps -o lstart=` prints it, in local time or, as `TZ=UTC ps` prints it, in UTC."""
    return time.strftime(LSTART, time.gmtime(seconds) if utc else time.localtime(seconds))


def command(pid: int) -> str | None:
    """The process's arguments joined by spaces, as `ps -o command=` prints them, None when no process holds pid or this user may not read them."""
    if sys.platform == "darwin":
        return darwin_command(pid)
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except UNREADABLE:
        return None
    return shown(raw.rstrip(b"\0").split(b"\0"))


def darwin_table() -> list[Process]:
    system = libsystem()
    pids = (ctypes.c_int * (system.proc_listallpids(None, 0) + SPARE_PIDS))()
    found = []
    for pid in pids[: system.proc_listallpids(pids, ctypes.sizeof(pids))]:
        info = ShortInfo()
        if system.proc_pidinfo(pid, PROC_PIDT_SHORTBSDINFO, WITH_ZOMBIES,ctypes.byref(info), ctypes.sizeof(info)) == ctypes.sizeof(info):
            found.append(Process(info.pid, info.ppid, info.pgid, info.status == SZOMB))
    return found


def darwin_command(pid: int) -> str | None:
    system = libsystem()
    size = ctypes.c_size_t(argument_bytes())
    buffer = ctypes.create_string_buffer(size.value)
    if system.sysctl((ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, pid), 3, buffer, ctypes.byref(size), None, 0):
        return None
    raw = buffer.raw[: size.value]
    count = int.from_bytes(raw[:4], sys.byteorder)
    words = raw[4:].partition(b"\0")[2].lstrip(b"\0").split(b"\0")[:count]
    return shown(words)


@cache
def argument_bytes() -> int:
    """The most bytes a process's arguments and environment take, KERN_ARGMAX, the buffer KERN_PROCARGS2 fills."""
    most = ctypes.c_int()
    size = ctypes.c_size_t(ctypes.sizeof(most))
    if libsystem().sysctl((ctypes.c_int * 2)(CTL_KERN, KERN_ARGMAX), 2, ctypes.byref(most), ctypes.byref(size), None, 0):
        raise OSError(ctypes.get_errno(), "sysctl kern.argmax failed")
    return most.value


def proc_pids() -> list[int]:
    return [int(entry) for entry in os.listdir("/proc") if entry.isdigit()]


def proc_stat(pid: int) -> list[str] | None:
    """The fields of /proc/<pid>/stat past its command name, which may hold spaces and parentheses, so the state is the first."""
    try:
        return Path(f"/proc/{pid}/stat").read_bytes().rpartition(b")")[2].decode().split()
    except UNREADABLE:
        return None


@cache
def boot_time() -> int:
    return next(int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines() if line.startswith("btime "))


def shown(words: list[bytes]) -> str:
    """Arguments joined by spaces, each unprintable character shown as ?, as `ps` keeps a newline inside an argument off its line."""
    return " ".join("".join(character if character.isprintable() else "?" for character in word.decode(errors="replace")) for word in words)
