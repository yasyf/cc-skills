import os
import time

from drive import procs

UNUSED_PID = 4_194_305


def test_the_process_table_holds_this_process_with_its_parent_and_group_read_in_process() -> None:
    [own] = [process for process in procs.table() if process.pid == os.getpid()]
    assert (own.ppid, own.pgid, own.zombie) == (os.getppid(), os.getpgrp(), False)


def test_a_process_start_is_read_in_whole_seconds_and_printed_as_ps_lstart_prints_it() -> None:
    started = procs.started(os.getpid())
    assert started is not None and time.time() - 24 * 3600 < started <= time.time()
    assert procs.lstart(0, utc=True) == "Thu Jan  1 00:00:00 1970"
    assert procs.started(UNUSED_PID) is None


def test_a_process_command_is_its_arguments_joined_by_spaces() -> None:
    assert "pytest" in procs.command(os.getpid())
