from cryptography.fernet import Fernet

from app.config import settings

_fernet = Fernet(settings.session_encryption_key.encode())


def encrypt_session(session_string: str) -> str:
    return _fernet.encrypt(session_string.encode()).decode()


def decrypt_session(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()
