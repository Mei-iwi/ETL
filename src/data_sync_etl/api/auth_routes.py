from dataclasses import asdict

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from data_sync_etl.logging import event
from data_sync_etl.security.auth import AuthError

router = APIRouter(prefix='/api/v1/auth', tags=['Authentication'])


class LoginInput(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024, repr=False)
    remember_me: bool = False


def service(request):
    auth = getattr(request.app.state, 'auth', None)
    if auth is None:
        raise AuthError('AUTH_NOT_CONFIGURED', 503)
    return auth


@router.get('/config')
def config(request: Request, response: Response):
    response.headers['Cache-Control'] = 'private, no-store'
    settings = request.app.state.container.settings
    return {'mode': settings.auth_mode, 'login_required': settings.auth_mode == 'single_admin',
            'admin_feature_enabled': settings.admin_enabled,
            'remember_supported': settings.auth_mode == 'single_admin'}


@router.get('/csrf')
def csrf(request: Request, response: Response):
    auth = service(request)
    seed = auth.csrf_seed()
    response.set_cookie(auth.csrf_cookie, seed, max_age=auth.settings.auth_remember_seconds, **auth.cookie_options())
    response.headers['Cache-Control'] = 'private, no-store'
    return {'csrf_token': auth.csrf_token(seed)}


@router.post('/login')
def login(body: LoginInput, request: Request, response: Response):
    auth = service(request)
    token, absolute = auth.login(request, body.email, body.password, body.remember_me)
    response.set_cookie(auth.cookie_name, token, max_age=absolute if body.remember_me else None, **auth.cookie_options())
    response.headers['Cache-Control'] = 'private, no-store'
    return {'authenticated': True, 'role': 'ADMIN'}


@router.get('/me')
def me(request: Request, response: Response):
    auth = service(request)
    response.headers['Cache-Control'] = 'private, no-store'
    return {**asdict(auth.principal(request)), 'admin_feature_enabled': auth.settings.admin_enabled}


@router.post('/logout', status_code=204)
def logout(request: Request):
    auth = service(request)
    token = request.cookies.get(auth.cookie_name, '')
    if token:
        auth.store.revoke(auth.digest(token.encode()))
    event(stage='authentication', action='LOGOUT')
    response = Response(status_code=204, headers={'Cache-Control': 'private, no-store'})
    for name in (auth.cookie_name, auth.csrf_cookie):
        response.delete_cookie(name, **auth.cookie_options())
    return response
