"""Watch the local invoice drop folder and sync completed PDF files."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from src.extractor import sync_pdf_to_supabase

LOGGER = logging.getLogger(__name__)


def wait_for_stable_pdf(
    path: str | Path,
    *,
    timeout: float = 60.0,
    interval: float = 0.5,
    stable_checks: int = 3,
) -> bool:
    """Wait until a copied PDF has a nonzero size that remains unchanged."""
    file_path = Path(path)
    deadline = time.monotonic() + timeout
    previous: tuple[int, int] | None = None
    stable_count = 0
    while time.monotonic() < deadline:
        try:
            stat = file_path.stat()
        except FileNotFoundError:
            previous = None
            stable_count = 0
        else:
            current = (stat.st_size, stat.st_mtime_ns)
            if stat.st_size > 0 and current == previous:
                stable_count += 1
                if stable_count >= stable_checks:
                    return True
            else:
                stable_count = 0
            previous = current
        time.sleep(interval)
    return False


class _InvoiceDropHandler(FileSystemEventHandler):
    def __init__(self, processor: Callable[[str | Path], object]) -> None:
        self._processor = processor
        self._pending: set[Path] = set()
        self._lock = threading.Lock()

    def _schedule(self, path: str | Path) -> None:
        file_path = Path(path)
        if file_path.suffix.lower() != ".pdf":
            return
        with self._lock:
            if file_path in self._pending:
                return
            self._pending.add(file_path)
        threading.Thread(target=self._process, args=(file_path,), daemon=True).start()

    def _process(self, path: Path) -> None:
        try:
            if not wait_for_stable_pdf(path):
                LOGGER.error("Invoice PDF did not finish copying before timeout: %s", path)
                return
            result = self._processor(path)
            LOGGER.info("Synced inbound invoice PDF %s: %s", path.name, result)
        except Exception:
            LOGGER.exception("Could not process inbound invoice PDF %s", path)
        finally:
            with self._lock:
                self._pending.discard(path)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule(event.dest_path)


def start_inbound_watcher(
    inbound_dir: str | Path,
    processor: Callable[[str | Path], object] = sync_pdf_to_supabase,
) -> Observer:
    """Start watching a folder and process PDFs already present in it."""
    source_dir = Path(inbound_dir)
    source_dir.mkdir(parents=True, exist_ok=True)
    handler = _InvoiceDropHandler(processor)
    observer = Observer()
    observer.schedule(handler, str(source_dir), recursive=False)
    observer.start()
    for pdf_path in source_dir.glob("*.pdf"):
        handler._schedule(pdf_path)
    return observer