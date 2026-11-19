"""GET /health: report whether the API and its dependencies are working."""

from fastapi import APIRouter, Response, status

from app.deps import QdrantDep, SessionDep
from app.schemas import HealthResponse
from app.services import health_service

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="Health check",
    description=(
        "Checks that Neon PostgreSQL and Qdrant respond within a few seconds. "
        "Returns 200 when both are ok, 503 when either fails. Public, no token needed; "
        "used by Docker and uptime monitors."
    ),
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": HealthResponse,
            "description": "At least one dependency is unreachable",
        }
    },
)
async def health(session: SessionDep, qdrant: QdrantDep, response: Response) -> HealthResponse:
    """Run the health checks and set 503 if any failed.

    Args:
        session: Database session for this request.
        qdrant: The shared Qdrant client.
        response: Lets the route change the status code of a normal response.

    Returns:
        Overall and per-dependency status.
    """
    result = await health_service.check_health(session, qdrant)
    if result.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
