import os
import re
import json
import requests
from sys import argv
from statistics import mean, median
from functools import cmp_to_key
from ratelimit import limits, sleep_and_retry

DECK_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "decks")
RAW_COLUMNS = ("amount", "set", "number", "lang", "foil")
API_LINK = "https://api.scryfall.com/cards/{set}/{number}/{lang}"

decks = set()  # all decks to search
elements = []  # all elements to print
stats = []  # all global stats to print
filters = set()  # all filters applied to cards
flags = set()  # all flags set
cards = []  # all cards found

ELEMENTS = {
    "amount": True,
    "foil": False,
    "name": False,
    "lang": False,
    "cost": False,
    "cmc": True,
    "type": False,
    "subtype": False,
    "color": False,
    "identity": False,
    "modern": False,
    "commander": False,
    "set": False,
    "number": False,
    "rarity": False,
    "fullart": False,
    "usd": True,
    "eur": True,
}

PREFIXES = {
    "-total-",
    "-max-",
    "-min-",
    "-avg-",
    "-median-",
}

FILTERS = {
    "=": (True, True),
    "!=": (True, True),
    "@": (True, False),
    "!@": (True, False),
    "<": (False, True),
    "<=": (False, True),
    ">": (False, True),
    ">=": (False, True),
}

FLAGS = {
    "decks",
    "unique",
    "get",
}

# parse arguments
for arg in argv[1:]:
    prefix, element, show, search = re.match(
        "(.*-)?([^?=!@<>]+)?(\\?)?(.+)?", arg
    ).groups()
    if search is not None:
        search = [re.match("([=!@<>]+)?(.+)?", x).groups() for x in search.split("/")]

    match (prefix, element, show, search):
        # stat element
        case (p, e, None, None) if p in PREFIXES and e in ELEMENTS and ELEMENTS[e]:
            stats.append((e, p))
        # filtered countable element
        case ("-", e, q, l) if e in ELEMENTS and ELEMENTS[e] and l and all(
            f in FILTERS and FILTERS[f][1] for f, _ in l
        ):
            filters.add((e, tuple((f, float(v)) for f, v in l)))
            if q is None:
                elements.append(e)
        # filtered uncountable element
        case ("-", e, q, l) if e in ELEMENTS and not ELEMENTS[e] and l and all(
            f in FILTERS and FILTERS[f][0] for f, _ in l
        ):
            filters.add((e, tuple((f, v.replace("_", " ")) for f, v in l)))
            if q is None:
                elements.append(e)
        # unfiltered element
        case ("-", e, None, None) if e in ELEMENTS:
            elements.append(e)
        # flags
        case ("-", f, None, None) if f in FLAGS:
            flags.add(f)
        # asterisk for all cards
        case (None, "*", None, None):
            decks.add("collection")
        # deck
        case (None, e, None, None):
            decks.add(e)
        # unable to match
        case _:
            print(f"ERROR: '{arg}' is not a recognized command")
            exit(1)


# max 10 api calls every second
@sleep_and_retry
@limits(calls=10, period=1)
def get_card(card):
    uri = API_LINK.format(**card)
    response = requests.get(uri)
    response.raise_for_status()
    return response.json()


for deck in decks:
    raw_path = os.path.join(DECK_DIR, deck, "raw.txt")
    data_path = os.path.join(DECK_DIR, deck, "data.json")

    if "get" not in flags and os.path.isfile(data_path):
        with open(data_path, "r", encoding="utf-8") as data_file:
            cards.extend(json.loads(data_file.read()))
    else:
        with open(raw_path, "r", encoding="utf-8") as raw_file, open(
            data_path, "w", encoding="utf-8"
        ) as data_file:
            raw = [
                dict(zip(RAW_COLUMNS, line.rstrip("\n").split("\t")))
                for line in raw_file
            ]
            scry = [get_card(card) for card in raw]
            data = []

            for r, s in zip(raw, scry):
                if "card_faces" in s:
                    s = s["card_faces"][0] | s

                foil = r["foil"] == "TRUE"
                types = s["type_line"].split(" \u2014 ")
                data.append(
                    {
                        "amount": int(r["amount"]),
                        "foil": foil,
                        "name": s["name"],
                        "lang": s["lang"],
                        "cost": re.sub("[^WUBRG0-9]", "", s["mana_cost"]),
                        "cmc": int(s["cmc"]),
                        "type": types[0],
                        "subtype": types[1] if len(types) > 1 else None,
                        "color": "".join(s["colors"]),
                        "identity": "".join(s["color_identity"]),
                        "modern": s["legalities"]["modern"] == "legal",
                        "commander": s["legalities"]["commander"] == "legal",
                        "set": s["set_name"],
                        "number": s["collector_number"],
                        "rarity": s["rarity"],
                        "fullart": s["full_art"],
                        "usd": s["prices"]["usd_foil" if foil else "usd"],
                        "eur": s["prices"]["eur_foil" if foil else "eur"],
                    }
                )

            data_file.write(json.dumps(data))
            cards.extend(data)


# apply card filters
def apply_filter(apply_amount):
    global cards

    func_map = {  # filter function from filter operator
        "=": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
        "@": lambda a, b: b in a,
        "!@": lambda a, b: b not in a,
        ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b,
        "<=": lambda a, b: a <= b,
    }

    for e, f in filters:
        # apply most filters before collapsing but amount only after
        if (e == "amount") == apply_amount:
            # only keep the cards passing an option from all filters
            cards = [
                c
                for c in cards
                if any(
                    func_map[o](float(c[e]) if ELEMENTS[e] else str(c[e]), v)
                    for o, v in f
                )
            ]


apply_filter(False)

# collapse duplicate cards
collapsed = []
for c in cards:
    for o in collapsed:
        # if all relevant elements are equal they can be collapsed into one
        if all(c[e] == o[e] for e in elements + [e[0] for e in stats] if e != "amount"):
            o["amount"] += c["amount"]
            break
    else:
        # if they aren't, it is a separate card
        collapsed.append(c)

cards = collapsed
apply_filter(True)


# sort cards
def cmp(a, b):
    # sort by elements in the order they were specified
    for e in elements:
        if a[e] < b[e]:
            return -1
        elif b[e] < a[e]:
            return 1
    return 0


cards.sort(key=cmp_to_key(cmp))

if cards:
    # print prices differently
    if elements:
        styled = [
            {
                k: str(v)
                for k, v in {
                    **c,
                    "usd": "-" if c["usd"] is None else f"${c['usd']}",
                    "eur": "-" if c["eur"] is None else f"€{c['eur']}",
                    "subtype": "-" if c["subtype"] is None else c["subtype"],
                }.items()
            }
            for c in cards
        ]
        # get least width that will fit all elements in column
        widths = [max(map(lambda c: len(c[e]), styled)) for e in elements]
        # create the template to format cards
        template = (" " * 4).join(f"{{{e}:<{w}}}" for e, w in zip(elements, widths))
        # print all cards
        for c in styled:
            print(template.format(**c))
        # follow by newline
        print()

    def data(e):
        # remove the amount statistic to more easily apply the function
        if e == "amount":
            for c in cards:
                yield c[e]
        else:
            for c in cards:
                if c[e] is not None:
                    for _ in range(c["amount"]):
                        yield float(c[e])

    # print global statistics
    for e, m in stats:
        # function corresponding to modification
        func = {
            "-total-": sum,
            "-max-": max,
            "-min-": min,
            "-avg-": mean,
            "-median-": median,
        }[m]

        # add unit
        unit = ""
        if e == "usd":
            unit = "$"
        elif e == "eur":
            unit = "€"

        # print statistic
        print(f"{m.strip('-').capitalize()} {e}: {unit}{round(func(data(e)),2)}")

    # print amount of unique matches
    if "unique" in flags:
        print(f"Unique: {len(cards)}\n")
    # follow by newline
    elif stats:
        print()

elif decks:
    print("No matches found\n")


# print all available decks
if "decks" in flags:
    print("Saved decks:")
    # print cards in decks list
    for f in os.listdir(DECK_DIR):
        print(" " * 4 + os.path.splitext(f)[0])
    print()

exit(0)
