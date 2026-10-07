import argparse
import json
import sys

from .converter import convert_batch


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "workflow":
        from .workflow_cli import main as workflow_main
        return workflow_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description="Convert Sony ARW files to JPEG using rawpy or an explicit RawTherapee profile.")
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--engine", choices=("rawpy", "rawtherapee"), default="rawpy")
    parser.add_argument("--profile", help="Explicit RawTherapee PP3 processing profile")
    parser.add_argument("--rawtherapee-cli", help="Path to rawtherapee-cli executable")
    parser.add_argument("--quality", type=int, default=95)
    parser.add_argument("--max-size", type=int, default=None,
                        help="rawpy longest-edge limit; RawTherapee resizing must be set in PP3")
    args = parser.parse_args()
    try:
        convert = convert_batch
        options = {}
        if args.engine == "rawtherapee":
            from .rawtherapee import convert_batch as convert
            options = {"profile": args.profile, "executable": args.rawtherapee_cli}
        elif args.profile or args.rawtherapee_cli:
            raise ValueError("--profile and --rawtherapee-cli require --engine rawtherapee")
        manifest = convert(
            args.input, args.output, args.recursive, args.quality, args.max_size,
            progress=lambda done, total, item: print(
                f"[{done}/{total}] {item['status']}: {item['source']}"
                + (f" — {item['error']}" if item.get('error') else ""),
                file=sys.stderr, flush=True), **options)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"manifest": manifest["manifest"], **manifest["summary"]}, ensure_ascii=False))
    return 1 if manifest["summary"]["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
