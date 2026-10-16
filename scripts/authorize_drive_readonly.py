"""Interactive Desktop OAuth, loopback + PKCE; no database/ETL operation or secret logs."""

import argparse
import json
import logging
import os
import subprocess
from pathlib import Path

SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
REPOSITORY = Path(__file__).resolve().parents[1]


def validate_paths(client_file, token_file, *, repository=REPOSITORY):
    client, token = Path(client_file).resolve(strict=True), Path(token_file)
    if not token.is_absolute() or token.is_symlink() or token.exists():
        raise ValueError('TOKEN_TARGET_NOT_NEW')
    parent = token.parent.resolve(strict=True)
    if parent.is_relative_to(repository.resolve()) or client.is_relative_to(repository.resolve()):
        raise ValueError('CREDENTIAL_MUST_BE_OUTSIDE_REPOSITORY')
    # Reject a symlink/junction destination directory, not just its resolved path.
    if token.parent.is_symlink() or (hasattr(token.parent, 'is_junction') and token.parent.is_junction()):
        raise ValueError('TOKEN_DIRECTORY_UNSAFE')
    token = parent / token.name
    config = json.loads(client.read_text(encoding='utf-8-sig'))
    installed = config.get('installed')
    if (not isinstance(installed, dict)
            or installed.get('auth_uri') not in ('https://accounts.google.com/o/oauth2/auth', 'https://accounts.google.com/o/oauth2/v2/auth')
            or installed.get('token_uri') != 'https://oauth2.googleapis.com/token'):
        raise ValueError('DESKTOP_GOOGLE_CLIENT_REQUIRED')
    return config, token


def save_private_token(token, document):
    descriptor = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
            if os.name == 'nt':
                # Lock ACL before writing any secret. Keep file paths out of command output.
                identity = subprocess.run(['whoami'], capture_output=True, text=True, check=True).stdout.strip()
                subprocess.run(['icacls', str(token), '/inheritance:r', '/grant:r',
                                f'{identity}:(F)', 'SYSTEM:(F)'], capture_output=True, check=True)
            json.dump(document, target)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        # Only remove the newly created token, never the input client credential.
        token.unlink(missing_ok=True)
        raise


def authorize(client_file, token_file, *, timeout=300):
    # google-auth-oauthlib's callback logger can include the authorization code.
    # This dedicated process never emits library/HTTP logs or exception details.
    logging.disable(logging.CRITICAL)
    config, token = validate_paths(client_file, token_file)
    import requests
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(config, scopes=[SCOPE], autogenerate_code_verifier=True)
    print('OAUTH_BROWSER_OPENING: hay dang nhap va cap quyen chi doc Drive tren trinh duyet.', flush=True)
    credentials = flow.run_local_server(
        host='127.0.0.1', bind_addr='127.0.0.1', port=0, timeout_seconds=timeout,
        authorization_prompt_message=None, open_browser=True,
        success_message='Da nhan phan hoi Google. Ban co the dong cua so va xem ket qua trong terminal.',
        access_type='offline', prompt='consent', include_granted_scopes='false',
    )
    granted = set(credentials.granted_scopes or credentials.scopes or ())
    if granted != {SCOPE} or not credentials.refresh_token:
        raise ValueError('READONLY_REFRESH_CREDENTIAL_REQUIRED')
    # Verify refresh credentials without listing/accessing any Drive file or database.
    with requests.Session() as session:
        request = Request(session=session)
        def bounded_request(*args, **kwargs):
            kwargs['timeout'] = 20
            return request(*args, **kwargs)
        credentials.refresh(bounded_request)
    if not credentials.valid:
        raise ValueError('CREDENTIAL_NOT_VALID')
    document = json.loads(credentials.to_json())
    document['type'] = 'authorized_user'
    save_private_token(token, document)
    print('OAUTH_SUCCESS: credential authorized_user da luu rieng; refresh PASS; chi doc Drive.', flush=True)
    print('FOLDER_CHECK_PENDING: can folder ID; chua bat Drive hoac chay ETL.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-file', type=Path, required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=300, choices=range(30, 601))
    args = parser.parse_args()
    try:
        authorize(args.client_file, args.token_file, timeout=args.timeout)
    except KeyboardInterrupt:
        print('OAUTH_CANCELLED: khong hien thi hoac luu token.', flush=True)
        return 1
    except Exception as exc:
        # Only fixed application codes are safe to display; no OAuth response or traceback.
        safe_codes = {'TOKEN_TARGET_NOT_NEW', 'CREDENTIAL_MUST_BE_OUTSIDE_REPOSITORY',
                      'TOKEN_DIRECTORY_UNSAFE', 'DESKTOP_GOOGLE_CLIENT_REQUIRED',
                      'READONLY_REFRESH_CREDENTIAL_REQUIRED', 'CREDENTIAL_NOT_VALID'}
        code = str(exc) if isinstance(exc, ValueError) and str(exc) in safe_codes else 'OAUTH_NOT_COMPLETED'
        print(f'{code}: khong hien thi chi tiet bao mat; kiem tra trang Google/credential/runtime.', flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
