import os
import re
import json
import asyncio
import aiohttp
from sys import argv
from statistics import mean, median
from functools import cmp_to_key

# directory containing decks
DECK_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "decks")
# column order in raw data
RAW_COLUMNS = ("amount", "set", "number", "lang", "foil")
# format-able link to scryfall api
API_LINK = "https://api.scryfall.com/cards/{set}/{number}/{lang}"
# minimum time in seconds between api request
RATE_LIMIT = 0.1

decks = set()  # all decks to search
elements = []  # all elements to print
stats = []  # all global stats to print
filters = set()  # all filters applied to cards
flags = set()  # all flags set
cards = []  # all cards found

# card attribute name: card attribute is numeric
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

# global numeric attributes
PREFIXES = {
    "-total-",
    "-max-",
    "-min-",
    "-avg-",
    "-median-",
}

# attribute filters: (non-numeric compatibility, numeric compatibility)
FILTERS = {
    "=": (True, True),  # exact match
    "!=": (True, True),  # anything but exact match
    "?": (True, False),  # contains
    "!?": (True, False),  # does not contain
    "<": (False, True),  # strictly less than
    "<=": (False, True),  # less than or equal to
    ">": (False, True),  # strictly greater than
    ">=": (False, True),  # greater than or equal to
}

# standalone flags
FLAGS = {
    "unique",  # number of unique matches
    "decks",  # print all available decks
    "get",  # get new card information from API_LINK
}

# parse arguments
for arg in argv[1:]:
    prefix, element, show, search = re.match(
        "^(.*-)?([^#=?!<>]+)?(#)?(.+)?$", arg
    ).groups()
    if search is not None:
        search = [re.match("^([=?!<>]+)?(.*)$", x).groups() for x in search.split("/")]

    match (prefix, element, show, search):
        # stat element
        case (p, e, None, None) if p in PREFIXES and e in ELEMENTS and ELEMENTS[e]:
            stats.append((e, p))
        # filtered countable element
        case ("-", e, q, l) if e in ELEMENTS and ELEMENTS[e] and l and all(
            f in FILTERS and FILTERS[f][1] for f, v in l
        ):
            for _, v in l:
                if not re.match("^[0-9]+(_[0-9]+)?$", v):
                    print(f"ERROR: '{v}' is not a number")
                    exit(1)

            if not err:
                filters.add((e, tuple((f, float(v.replace("_", "."))) for f, v in l)))
            if q is None:
                elements.append(e)
        # filtered uncountable element
        case ("-", e, q, l) if e in ELEMENTS and not ELEMENTS[e] and l and all(
            f in FILTERS and FILTERS[f][0] for f, v in l
        ):
            filters.add((e, tuple(l)))
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


# get raw card from API_LINK using session
async def get_card(card, session):
    url = API_LINK.format(**card)
    async with session.get(url) as response:
        res = await response.json()
        if response.ok:
            return res
        else:
            print(f"ERROR: '{url}' returned http code {response.status}")
            return None


# get raw cards in parallel
async def get_cards(cards):
    async with aiohttp.ClientSession() as session:
        tasks = []
        for card in cards:
            task = asyncio.create_task(get_card(card, session))
            tasks.append(task)
            # be nice to their server (and don't get a rate limit)
            await asyncio.sleep(RATE_LIMIT)
        return await asyncio.gather(*tasks)


# get card data
for deck in decks:
    # path to raw card data
    raw_path = os.path.join(DECK_DIR, deck, "raw.txt")
    # path to json with api data
    data_path = os.path.join(DECK_DIR, deck, "data.json")

    if "get" not in flags:  # load from file
        if not os.path.isfile(data_path):  # file not found
            print(f"ERROR: Could not find '{data_path}'. Use '-get' to generate it")
            exit(1)

        with open(data_path, "r", encoding="utf-8") as data_file:
            try:  # add new cards as dictionaries
                cards.extend(json.loads(data_file.read()))
            except ValueError as e:  # failed to load json
                print(
                    f"ERROR: '{data_path}' contains invalid json. Use '-get' to generate it"
                )
                exit(1)

    else:  # get cards from api
        if not os.path.isfile(raw_path):  # file not found
            print(f"ERROR: Could not find '{raw_path}'")
            exit(1)

        with open(raw_path, "r", encoding="utf-8") as raw_file, open(
            data_path, "w", encoding="utf-8"
        ) as data_file:
            raw = [  # split columns into dictionary
                dict(zip(RAW_COLUMNS, line.rstrip("\n").split("\t")))
                for line in raw_file
            ]

            # check if any columns missing
            if any(len(line) < len(RAW_COLUMNS) for line in raw):
                print(f"ERROR: too few columns in '{raw_path}'")
                exit(1)

            for line in raw:  # check that all amounts are valid
                if not line["amount"].isdigit():
                    print(f"ERROR: '{line['amount']}' is not a valid amount")
                    exit(1)

            # get multiple cards at once
            scry = asyncio.run(get_cards(raw))
            if None in scry:  # bad response
                exit(1)

            data = []  # all cards from deck
            for r, s in zip(raw, scry):
                if "card_faces" in s:  # default to front face on double sided cards
                    s = s["card_faces"][0] | s

                foil = r["foil"] == "TRUE"  # wether card is foil or not
                types = s["type_line"].split(" \u2014 ")  # type - subtype
                data.append(  # process information about card
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

            # save data to not have to -get next time
            data_file.write(json.dumps(data))
            cards.extend(data)


# apply card filters
def apply_filter(apply_amount):
    global cards

    func_map = {  # filter function from filter operator
        "=": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
        "?": lambda a, b: b in a,
        "!?": lambda a, b: b not in a,
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
                if c[e] is not None
                and any(
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

    def expand(e):
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
        print(f"{m.strip('-').capitalize()} {e}: {unit}{round(func(expand(e)),2)}")

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
