"""Separate optional read path; Search response and latency stay unchanged."""

from typing import Annotated

from fastapi import APIRouter, Path, Request, Response
from starlette.concurrency import run_in_threadpool

from data_sync_etl.api.search_access import search_access

router = APIRouter(tags=['Search'])


@router.get('/api/v1/resources/{resource_id}/related-learning')
async def related_learning(resource_id: Annotated[str, Path(min_length=1, max_length=30)],
                           request: Request, response: Response):
    access = await run_in_threadpool(search_access, request)
    response.headers['Cache-Control'] = 'private, no-store'
    service = getattr(request.app.state.container, 'related_learning', None)
    if service is None:
        return {'items': []}
    items = await run_in_threadpool(service.run, resource_id, access)
    # The officially authenticated identity/grants must still match after I/O.
    if access != await run_in_threadpool(search_access, request):
        return {'items': []}
    return {'items': items}
