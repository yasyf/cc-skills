import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from long_running import ruling_documents, rulings


@pytest.fixture
def counted_open(monkeypatch):
    original = Path.open
    counts = {"path": None, "bytes": 0}

    class Counted(io.FileIO):
        def read(self, size=-1):
            data = super().read(size)
            counts["bytes"] += len(data)
            return data

        def readall(self):
            data = super().readall()
            counts["bytes"] += len(data)
            return data

        def readinto(self, buffer):
            size = super().readinto(buffer)
            counts["bytes"] += size
            return size

    def opened(path, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        if path != counts["path"]:
            return original(path, mode, buffering, encoding, errors, newline)
        assert mode == "rb"
        return io.BufferedReader(Counted(path, "r"), buffer_size=io.DEFAULT_BUFFER_SIZE if buffering == -1 else buffering)

    monkeypatch.setattr(Path, "open", opened)
    return counts


def bounded(caller, tmp_path, counted_open, size, nul_at):
    path = tmp_path / "binary.md"
    path.write_bytes(b"a" * nul_at + b"\0" + b"b" * (size - nul_at - 1))
    counted_open.update(path=path, bytes=0)
    documents = ruling_documents.DocumentIndex(rulings.sweep_words, None)
    sweeper = rulings.Sweeper(SimpleNamespace(answer_words=lambda: {}), {"rule": {"names": json.dumps(["needle"])}}, documents if caller == "classify" else None)
    read = lambda: rulings.readable(path) if caller == "reader" else documents.lines(path, None) if caller == "index" else sweeper.file(path, "binary.md")
    result = read()
    assert result is None if caller == "reader" else result == []
    consumed = counted_open["bytes"]
    print(json.dumps({"caller": caller, "file_bytes": size, "nul_at": nul_at, "read_bytes": consumed, "rulings_source": hashlib.sha256(Path(rulings.__file__).read_bytes()).hexdigest(), "document_source": hashlib.sha256(Path(ruling_documents.__file__).read_bytes()).hexdigest(), "scope": "counted real FileIO body reads; setup and metadata excluded, no physical IO claim"}))
    assert consumed <= 4096, f"binary ruling {caller} read {consumed} bytes before rejecting first4096 NUL"
    if caller in {"index", "classify"}:
        assert read() == []
        assert counted_open["bytes"] == consumed


@pytest.mark.parametrize(("size", "nul_at"), [(32 * 1024, 0), (8 * 1024 * 1024, 0), (8 * 1024 * 1024, 4095)], ids=["small", "large", "boundary"])
def test_ruling_reader_binary_prefix(tmp_path, counted_open, size, nul_at):
    bounded("reader", tmp_path, counted_open, size, nul_at)


@pytest.mark.parametrize(("size", "nul_at"), [(32 * 1024, 0), (8 * 1024 * 1024, 0), (8 * 1024 * 1024, 4095)], ids=["small", "large", "boundary"])
def test_document_index_binary_prefix(tmp_path, counted_open, size, nul_at):
    bounded("index", tmp_path, counted_open, size, nul_at)


@pytest.mark.parametrize(("size", "nul_at"), [(32 * 1024, 0), (8 * 1024 * 1024, 0), (8 * 1024 * 1024, 4095)], ids=["small", "large", "boundary"])
def test_readback_sweeper_binary_prefix(tmp_path, counted_open, size, nul_at):
    bounded("readback", tmp_path, counted_open, size, nul_at)


@pytest.mark.parametrize(("size", "nul_at"), [(32 * 1024, 0), (8 * 1024 * 1024, 0), (8 * 1024 * 1024, 4095)], ids=["small", "large", "boundary"])
def test_classify_sweeper_binary_prefix(tmp_path, counted_open, size, nul_at):
    bounded("classify", tmp_path, counted_open, size, nul_at)


@pytest.mark.parametrize("data", [b"", b"needle\r\nnext\rfinal\n", b"\xef\xbb\xbfneedle\n", b"a" * 4095 + "€needle".encode() + b"\r\n", b"a" * 4095 + b"\xffneedle\r\n", b"a" * 4096 + b"\0needle\n", b"needle\r\n" * 20000])
def test_ruling_reader_preserves_complete_replacement_decoding_and_newlines(tmp_path, counted_open, data):
    path = tmp_path / "text.md"
    path.write_bytes(data)
    counted_open.update(path=path, bytes=0)
    expected = data.decode(errors="replace")
    assert rulings.readable(path) == expected
    assert counted_open["bytes"] == len(data)
    counted_open["bytes"] = 0
    documents = ruling_documents.DocumentIndex(rulings.sweep_words, None)
    assert documents.lines(path, None) == list(enumerate(expected.splitlines(), start=1))
    assert counted_open["bytes"] == len(data)


def test_ruling_reader_keeps_missing_file_and_index_invalidation_behavior(tmp_path):
    path = tmp_path / "missing.md"
    with pytest.raises(FileNotFoundError):
        rulings.readable(path)
    documents = ruling_documents.DocumentIndex(rulings.sweep_words, None)
    assert documents.lines(path, None) == []
    path.write_bytes(b"old needle\n")
    assert documents.lines(path, None) == [(1, "old needle")]
    replacement = tmp_path / "replacement.md"
    replacement.write_bytes(b"new needle\r\n")
    replacement.replace(path)
    assert documents.lines(path, None) == [(1, "new needle")]
    path.unlink()
    assert documents.lines(path, None) == [] and not documents.documents
