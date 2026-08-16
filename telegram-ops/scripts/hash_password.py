import getpass, base64
import sys, os, hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)

def hash_password(password: str, *, iterations: int = 260_000) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"

def main() -> None:
    password = getpass.getpass("Admin password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("passwords do not match")
    print(hash_password(password))


if __name__ == "__main__":
    main()
