"""Unit tests for the per-file upload lock in document_service."""

import asyncio
import uuid

from app.services import document_service
from app.services.document_service import _upload_lock


async def test_same_user_and_hash_run_one_at_a_time() -> None:
    owner = uuid.uuid4()
    events: list[str] = []

    async def upload(name: str) -> None:
        async with _upload_lock(owner, "abc"):
            events.append(f"{name} start")
            await asyncio.sleep(0.01)  # give the other task a chance to cut in
            events.append(f"{name} end")

    await asyncio.gather(upload("a"), upload("b"))
    assert events == ["a start", "a end", "b start", "b end"]


async def test_different_files_do_not_wait_for_each_other() -> None:
    owner = uuid.uuid4()
    events: list[str] = []

    async def upload(sha: str) -> None:
        async with _upload_lock(owner, sha):
            events.append(f"{sha} start")
            await asyncio.sleep(0.01)
            events.append(f"{sha} end")

    await asyncio.gather(upload("a"), upload("b"))
    assert events[:2] == ["a start", "b start"]


async def test_lock_entry_is_removed_when_free() -> None:
    owner = uuid.uuid4()
    async with _upload_lock(owner, "abc"):
        assert (owner, "abc") in document_service._upload_locks
    assert (owner, "abc") not in document_service._upload_locks
