"""FastAPI dependencies shared by the routes.

Routes ask for what they need by type (``session: SessionDep``) and FastAPI
supplies it. Tests replace these with fakes via ``app.dependency_overrides``.
"""

from typing import Annotated

from fastapi import Depends, Request
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db


def get_qdrant(request: Request) -> AsyncQdrantClient:
    """Return the single Qdrant client created in the app's lifespan.

    Args:
        request: The current request, used to reach ``app.state``.

    Returns:
        The shared `AsyncQdrantClient`.
    """
    return request.app.state.qdrant


SessionDep = Annotated[AsyncSession, Depends(get_db)]
QdrantDep = Annotated[AsyncQdrantClient, Depends(get_qdrant)]
