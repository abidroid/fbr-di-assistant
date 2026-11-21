"""Document endpoints: upload, list, get and delete (own documents only)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, Response, UploadFile, status

from app.deps import CurrentUser, PipelineDep, QdrantDep, SessionDep, SessionFactoryDep
from app.models import Document
from app.schemas import DocumentRead, error_responses
from app.services import document_service

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "",
    summary="Upload and ingest a document",
    description=(
        "Upload a PDF, DOCX, PPTX, TXT or MD file (max 25 MB). The file type is checked from "
        "its content, not its name. The file is extracted, chunked, embedded and stored for "
        "search.\n\n"
        "- **201**: ingested; `status` is `ready`.\n"
        "- **202**: a large document (more than `SYNC_INGEST_MAX_CHUNKS` chunks) is still being "
        "embedded; `status` is `processing`. Poll `GET /documents/{id}` until it is `ready`.\n"
        "- **409**: you already uploaded this exact file; the message and the `Location` header "
        "give the existing document's id."
    ),
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        202: {"model": DocumentRead, "description": "Accepted; still being embedded"},
        **error_responses(400, 401, 409, 413, 415, 422, 503),
    },
)
async def upload_document(
    file: Annotated[UploadFile, File(description="The document to upload.")],
    response: Response,
    background_tasks: BackgroundTasks,
    session: SessionDep,
    user: CurrentUser,
    pipeline: PipelineDep,
    session_factory: SessionFactoryDep,
) -> Document:
    """Upload a document and ingest it.

    Args:
        file: The uploaded file.
        response: Lets the route switch the status to 202.
        background_tasks: Runs large ingestions after the response.
        session: Database session for this request.
        user: The logged-in user (owner of the new document).
        pipeline: Extractor, embedders and Qdrant client.
        session_factory: Database sessions for the background task.

    Returns:
        The new document (serialised as `DocumentRead`).
    """
    document, finished = await document_service.upload_document(
        session,
        user,
        file,
        file.filename or "upload",
        pipeline,
        background_tasks.add_task,
        session_factory,
    )
    if not finished:
        response.status_code = status.HTTP_202_ACCEPTED
    return document


@router.get(
    "",
    summary="List your documents",
    description="Returns every document you uploaded, newest first, with its ingestion status.",
    response_model=list[DocumentRead],
    status_code=status.HTTP_200_OK,
    responses=error_responses(401, 503),
)
async def list_documents(session: SessionDep, user: CurrentUser) -> list[Document]:
    """List the user's documents.

    Args:
        session: Database session for this request.
        user: The logged-in user.

    Returns:
        Their documents.
    """
    return await document_service.list_documents(session, user)


@router.get(
    "/{doc_id}",
    summary="Get one document",
    description=(
        "Returns one of your documents, including its status; poll this after a 202 upload. "
        "Another user's document id returns 404, exactly like an id that does not exist."
    ),
    response_model=DocumentRead,
    status_code=status.HTTP_200_OK,
    responses=error_responses(401, 404, 422, 503),
)
async def get_document(doc_id: uuid.UUID, session: SessionDep, user: CurrentUser) -> Document:
    """Return one of the user's documents.

    Args:
        doc_id: The document's id.
        session: Database session for this request.
        user: The logged-in user.

    Returns:
        The document.
    """
    return await document_service.get_document(session, user, doc_id)


@router.delete(
    "/{doc_id}",
    summary="Delete a document",
    description=(
        "Deletes one of your documents: its vectors in the search index, the stored file and "
        "the database row. Afterwards nothing from it can be retrieved."
    ),
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(401, 404, 422, 503),
)
async def delete_document(
    doc_id: uuid.UUID, session: SessionDep, user: CurrentUser, qdrant: QdrantDep
) -> Response:
    """Delete one of the user's documents.

    Args:
        doc_id: The document's id.
        session: Database session for this request.
        user: The logged-in user.
        qdrant: Vector database client.

    Returns:
        An empty 204 response.
    """
    await document_service.delete_document(session, user, doc_id, qdrant)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
