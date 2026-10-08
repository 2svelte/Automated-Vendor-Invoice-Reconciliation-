import threading

from src.inbound_watcher import start_inbound_watcher


def test_watcher_processes_existing_and_new_pdfs(tmp_path):
    inbound_dir = tmp_path / "inbound"
    inbound_dir.mkdir()
    (inbound_dir / "existing.pdf").write_bytes(b"existing pdf")
    processed = []
    processed_event = threading.Event()

    def processor(path):
        processed.append(path.name)
        if len(processed) == 2:
            processed_event.set()

    observer = start_inbound_watcher(inbound_dir, processor=processor)
    try:
        (inbound_dir / "new.pdf").write_bytes(b"new pdf")
        assert processed_event.wait(5)
    finally:
        observer.stop()
        observer.join()

    assert set(processed) == {"existing.pdf", "new.pdf"}