from src.config import SETTINGS
from wsgi import application


if __name__ == "__main__":
    try:
        from waitress import serve
    except ImportError as exc:
        raise SystemExit(
            "Waitress is not installed. Run: .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt "
            "--extra-index-url https://download.pytorch.org/whl/cu121"
        ) from exc

    print("=" * 60)
    print("  VOX - Production Server")
    print("=" * 60)
    print(f"Host: {SETTINGS.host}")
    print(f"Port: {SETTINGS.port}")
    print(f"Threads: {SETTINGS.waitress_threads}")
    print("=" * 60)

    serve(
        application,
        host=SETTINGS.host,
        port=SETTINGS.port,
        threads=SETTINGS.waitress_threads,
    )
