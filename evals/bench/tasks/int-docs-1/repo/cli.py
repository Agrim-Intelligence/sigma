"""Print a greeting; a small command-line tool."""
import argparse


def build_parser():
    parser = argparse.ArgumentParser(prog="greet", description="Print a greeting.")
    parser.add_argument("name", help="who to greet")
    parser.add_argument("--greeting", default="Hello", help="the word that opens the line")
    parser.add_argument("--punctuation", default="!", help="what closes the line")
    parser.add_argument("--repeat", type=int, default=1, help="how many times to print the line")
    parser.add_argument("--upper", action="store_true", help="print the line in capitals")
    return parser


def render(args):
    line = f"{args.greeting}, {args.name}{args.punctuation}"
    if args.upper:
        line = line.upper()
    return "\n".join([line] * args.repeat)


def main(argv=None):
    print(render(build_parser().parse_args(argv)))


if __name__ == "__main__":
    main()
