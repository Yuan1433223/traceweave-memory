import argparse
import json
from pathlib import Path

from traceweave_memory.config import Settings


def main():
    parser = argparse.ArgumentParser(prog="traceweave")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="serve the memory API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    download = subparsers.add_parser("download-gallery", help="download a pinned public subset")
    download.add_argument("--metadata", type=Path, required=True)
    download.add_argument("--output", type=Path, default=Path("data/mem-gallery"))
    download.add_argument("--topics", nargs="+", required=True)
    download.add_argument("--max-mib", type=int, default=200)
    evaluate = subparsers.add_parser("evaluate-gallery", help="run offline B0 retrieval")
    evaluate.add_argument("--dataset", type=Path, default=Path("data/mem-gallery"))
    evaluate.add_argument("--topic", required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "serve":
        settings = Settings.from_env()
        if not settings.api_key and args.host not in {"127.0.0.1", "localhost", "::1"}:
            parser.error("set TW_API_KEY before binding a non-loopback interface")
        import uvicorn

        from traceweave_memory.bootstrap import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port, access_log=False)
    elif args.command == "download-gallery":
        from traceweave_memory.evaluation.download import download_subset

        manifest = download_subset(
            args.metadata, args.output, args.topics, args.max_mib * 1024 * 1024
        )
        print(
            json.dumps(
                {
                    "revision": manifest["revision"],
                    "topics": manifest["topics"],
                    "files": len(manifest["files"]),
                }
            )
        )
    elif args.command == "evaluate-gallery":
        from traceweave_memory.evaluation.runner import run_gallery

        print(json.dumps(run_gallery(args.dataset, args.topic, args.output), indent=2))


if __name__ == "__main__":
    main()
