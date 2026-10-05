DEFAULTS = {"name": "app", "tags": ["base"], "verbose": False}


def parse_args(argv):
    cfg = dict(DEFAULTS)
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--name":
            cfg["name"] = argv[i + 1]
            i += 1
        elif a == "--verbose":
            cfg["verbose"] = True
        else:
            raise ValueError("unknown argument: " + a)
        i += 1
    return cfg
